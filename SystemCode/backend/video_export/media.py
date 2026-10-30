"""Responsibilities: Decode source timing, encode H.264 and copy existing audio for exports.
Implementation: Bounded PyAV/Pillow streaming; preserve VFR timestamps and audio packet payloads.
Related Modules: video_export.services; requirements-video.txt.
Declaration Index: frame_time; VideoReader; VideoReader.__init__; VideoReader.__iter__;
VideoReader.close; VideoWriter; VideoWriter.__init__; VideoWriter.write; VideoWriter.mux;
VideoWriter.close; packet_sequence; copy_audio; audio_fingerprints; verify_video.
Variable Index: None. Source video selection is the first video stream; all audio tracks are copied.
"""

import hashlib
import heapq
import json
from contextlib import ExitStack
from fractions import Fraction

import av
from PIL import Image


def frame_time(frame):
    """Functionality: Read exact presentation time from a successfully decoded source frame.
    Inputs: PyAV video frame. Outputs: Fraction seconds or ValueError.
    Logic: Require PTS, positive time base and an uncorrupted frame.
    Constraints: No inferred timestamps or dropping damaged frames.
    """
    if frame.is_corrupt or frame.pts is None or frame.time_base is None or frame.time_base <= 0:
        raise ValueError("Video contains corrupt frames or missing presentation timestamps")
    return frame.pts * frame.time_base


class VideoReader:
    """Functionality: Read every video frame with its actual presentation duration.
    Logic: One-frame lookahead derives VFR durations; the final duration uses media metadata.
    Constraints: Fixed geometry and right-angle rotation; no seeking, frame limit or resampling.
    """

    def __init__(self, path):
        """Functionality: Validate source video before model loading.
        Inputs: Local path. Outputs: Open decoder, first frame and exact stream timing.
        Logic: Select first video; read pixel aspect ratio and reject missing media or invalid rate.
        Constraints: All original audio tracks are recorded for subsequent lossless remuxing.
        """
        self.container = av.open(str(path))
        try:
            if not self.container.streams.video:
                raise ValueError("Input contains no video stream")
            self.stream = self.container.streams.video[0]
            self.rate, self.time_base = self.stream.average_rate, self.stream.time_base
            if not self.rate or self.rate <= 0 or not self.time_base or self.time_base <= 0:
                raise ValueError("Video requires a positive frame rate and time base")
            self.pixel_aspect = self.stream.sample_aspect_ratio or Fraction(1)
            if self.pixel_aspect <= 0:
                raise ValueError("Video pixel aspect ratio must be positive")
            self.decoder = iter(self.container.decode(self.stream))
            self.first = next(self.decoder, None)
            if self.first is None:
                raise ValueError("Input contains no decodable video frames")
            self.origin = frame_time(self.first)
            self.geometry = (self.first.width, self.first.height, self.first.rotation)
            if self.first.rotation % 90:
                raise ValueError("Only right-angle video display rotations are supported")
            self.info = {"width": self.first.width, "height": self.first.height,
                         "rotation_degrees": self.first.rotation,
                         "pixel_aspect_ratio": str(self.pixel_aspect),
                         "frame_rate": str(self.rate), "time_base": str(self.time_base),
                         "first_timestamp": str(self.origin), "declared_frames": self.stream.frames,
                         "audio_streams": [{"index": s.index, "codec": s.codec_context.name}
                                           for s in self.container.streams.audio]}
        except BaseException:
            self.container.close()
            raise

    def __iter__(self):
        """Functionality: Yield frames, relative presentation seconds and exact durations.
        Inputs: Initialized decoder. Outputs: Ordered (frame, Fraction time, Fraction duration).
        Logic: Require strictly increasing PTS and declared frame-count agreement at EOF.
        Constraints: One pass only; no wall-clock timestamps, discarded tail or hidden repeats.
        """
        current, current_time, count = self.first, self.origin, 0
        for following in self.decoder:
            next_time = frame_time(following)
            if next_time <= current_time:
                raise ValueError("Video presentation timestamps must increase strictly")
            if (following.width, following.height, following.rotation) != self.geometry:
                raise ValueError("Video geometry or orientation changed mid-stream")
            yield current, current_time - self.origin, next_time - current_time
            count += 1
            current, current_time = following, next_time
        duration = current.duration * current.time_base
        if duration <= 0 and self.stream.duration is not None:
            start = (self.stream.start_time * self.time_base
                     if self.stream.start_time is not None else self.origin)
            duration = start + self.stream.duration * self.time_base - current_time
        if duration <= 0:
            raise ValueError("The final video frame has no valid presentation duration")
        yield current, current_time - self.origin, duration
        count += 1
        if self.stream.frames and count != self.stream.frames:
            raise ValueError("Decoded frame count differs from the declared video frame count")

    def close(self):
        """Functionality: Release the source decoder. Inputs: Reader. Outputs: None.
        Logic: Close only this container. Constraints: Never modify the source file.
        """
        self.container.close()


class VideoWriter:
    """Functionality: Encode every annotated frame into a browser-compatible H.264 MP4.
    Logic: Keep presentation times/durations; pad odd dimensions by at most one pixel.
    Constraints: Fixed libx264/yuv420p; no codec fallback or source-frame duplication.
    """

    def __init__(self, path, size, rate, time_base):
        """Functionality: Open a new encoder with explicit quality and timing settings.
        Inputs: Owned partial path, frame dimensions, nominal rate and source time base.
        Outputs: Open MP4 stream. Logic: Even dimensions, CRF20, fast preset, no B-frame delay.
        Constraints: Caller owns path exclusivity; missing libx264 fails explicitly.
        """
        self.container = av.open(str(path), "w", format="mp4", options={"movflags": "+faststart"})
        self.closed, self.pending = False, {}
        self.size = tuple(value + value % 2 for value in size)
        try:
            self.stream = self.container.add_stream("libx264", rate=rate)
            self.stream.width, self.stream.height = self.size
            self.stream.pix_fmt = "yuv420p"
            self.stream.codec_context.sample_aspect_ratio = Fraction(1)
            self.stream.time_base = self.stream.codec_context.time_base = time_base
            self.stream.codec_context.max_b_frames = 0
            self.stream.options = {"crf": "20", "preset": "fast", "tune": "zerolatency"}
        except BaseException:
            self.container.close()
            raise

    def write(self, image, timestamp, duration):
        """Functionality: Encode one annotated image without changing the video timeline.
        Inputs: RGB Pillow image and exact relative time/duration. Outputs: Muxed packets.
        Logic: Pad right/bottom if needed, then encode with explicit PTS and duration.
        Constraints: Timestamps must be exactly representable in the chosen source time base.
        """
        if image.size != self.size:
            padded = Image.new("RGB", self.size)
            padded.paste(image, (0, 0))
            image = padded
        frame = av.VideoFrame.from_image(image)
        frame.time_base = self.stream.codec_context.time_base
        pts, ticks = timestamp / frame.time_base, duration / frame.time_base
        if pts.denominator != 1 or ticks.denominator != 1:
            raise ValueError("Video timing cannot be represented without rounding")
        frame.pts, frame.duration = int(pts), int(ticks)
        self.pending[timestamp] = duration
        self.mux(self.stream.encode(frame))

    def mux(self, packets):
        """Functionality: Retain source frame duration through encoder packet buffering.
        Inputs: Encoded packets. Outputs: Muxed packets with explicit duration.
        Logic: Match packets by exact PTS; rescale only to the packet's time base.
        Constraints: Unknown encoder timestamps fail instead of silently changing cadence.
        """
        for packet in packets:
            duration = self.pending.pop(packet.pts * packet.time_base)
            ticks = duration / packet.time_base
            if ticks.denominator != 1:
                raise ValueError("Encoded frame duration would require rounding")
            packet.duration = int(ticks)
            self.container.mux(packet)

    def close(self):
        """Functionality: Flush delayed frames and finalize the MP4 index exactly once.
        Inputs: Encoder state. Outputs: Closed container or explicit flush error.
        Logic: Drain remaining packets, assert no pending frame and release in finally.
        Constraints: Partial files remain partial until the service verifies completion.
        """
        if self.closed:
            return
        self.closed = True
        try:
            self.mux(self.stream.encode(None))
            if self.pending:
                raise ValueError("Encoder did not emit every annotated frame")
        finally:
            self.container.close()


def packet_sequence(container, source, target, offset):
    """Functionality: Adapt one ordered stream for copying into the final MP4.
    Inputs: Source/target streams, container and video-origin offset in seconds.
    Outputs: (decode time, packet) pairs. Logic: Skip only empty flush packets; shift audio
    timing by the same video origin to the nearest audio tick and preserve packet payloads.
    Constraints: Missing timestamps fail; no audio synthesis, re-encoding or gain changes.
    """
    for packet in container.demux(source):
        if not packet.size:
            continue
        if packet.dts is None or packet.pts is None or packet.time_base is None:
            raise ValueError("Cannot copy an audio/video packet with missing timestamps")
        shift = round(offset / packet.time_base)
        packet.pts -= shift
        packet.dts -= shift
        time = packet.dts * packet.time_base
        packet.stream = target
        yield time, packet


def copy_audio(video_path, source_path, output_path, audio_streams, origin):
    """Functionality: Combine annotated video with all existing original audio tracks.
    Inputs: Owned silent MP4, original source, new output, source audio descriptors and origin.
    Outputs: Final partial MP4 with copied encoded audio. Logic: Independently read streams
    and interleave packets by decode time, preserving metadata and compressed payloads.
    Constraints: Unsupported MP4 audio codecs fail explicitly; never silently omit an audio track.
    """
    with ExitStack() as stack:
        video = stack.enter_context(av.open(str(video_path)))
        output = stack.enter_context(av.open(str(output_path), "w", format="mp4",
                                            options={"movflags": "+faststart",
                                                     "avoid_negative_ts": "disabled"}))
        target = output.add_stream_from_template(video.streams.video[0])
        sequences = [packet_sequence(video, video.streams.video[0], target, Fraction(0))]
        for descriptor in audio_streams:
            original = stack.enter_context(av.open(str(source_path)))
            source = original.streams[descriptor["index"]]
            target = output.add_stream_from_template(source)
            target.metadata.update(source.metadata)
            sequences.append(packet_sequence(original, source, target, origin))
        for _, packet in heapq.merge(*sequences, key=lambda item: item[0]):
            output.mux(packet)


def audio_fingerprints(path):
    """Functionality: Fingerprint encoded audio tracks without decoding or changing audio.
    Inputs: Local media path. Outputs: Ordered codec, packet-count and payload-hash records.
    Logic: Hash only nonempty compressed packets per audio stream in demux order.
    Constraints: Packet identity validates copying, not human audibility or loudness.
    """
    with av.open(str(path)) as container:
        streams = list(container.streams.audio)
        hashes = {stream.index: hashlib.sha256() for stream in streams}
        counts = dict.fromkeys(hashes, 0)
        if streams:
            for packet in container.demux(*streams):
                if packet.size:
                    hashes[packet.stream.index].update(bytes(packet))
                    counts[packet.stream.index] += 1
        return [{"codec": stream.codec_context.name, "packets": counts[stream.index],
                 "sha256": hashes[stream.index].hexdigest()} for stream in streams]


def verify_video(path, records_path, expected_frames, expected_size):
    """Functionality: Decode the complete export and compare it with its per-frame records.
    Inputs: Finished partial MP4/JSONL, expected count and padded size. Outputs: Video summary.
    Logic: Verify H.264, every frame/time, geometry and final duration before publication.
    Constraints: This validates media integrity, not detection or distance ground-truth accuracy.
    """
    with av.open(str(path)) as container, records_path.open(encoding="utf-8") as records:
        stream = container.streams.video[0]
        if stream.codec_context.name != "h264":
            raise ValueError("Export codec is not H.264")
        count, end = 0, Fraction(0)
        for frame in container.decode(stream):
            line = records.readline()
            if not line:
                raise ValueError("Export contains extra frames")
            row = json.loads(line)
            timestamp = frame_time(frame)
            tolerance = frame.time_base
            if (tuple(expected_size) != (frame.width, frame.height)
                    or abs(timestamp - Fraction(row["timestamp"])) > tolerance):
                raise ValueError("Export frame geometry or presentation time does not match")
            end = Fraction(row["timestamp"]) + Fraction(row["duration"])
            count += 1
        if count != expected_frames or records.readline():
            raise ValueError("Export is missing processed frames")
        if stream.duration is None or abs(stream.duration * stream.time_base - end) > tolerance:
            raise ValueError("Export duration differs from the source video timeline")
        return {"frames": count, "width": stream.width, "height": stream.height,
                "duration_s": float(end), "codec": "h264", "audio_tracks": len(
                    container.streams.audio)}
