/* Responsibilities: Own local optical-flow processing off the page's main thread.
 * Implementation: One bounded tracker, exact generation tags, content-free timings.
 * Related Modules: motion-core.js, jsfeat.min.js, local-motion.js.
 * Declaration Index: self.onmessage. Variable Index: tracker, generation, epoch; no network or persistence.
 */
self.window=self;
importScripts('./jsfeat.min.js','./motion-core.js');
const tracker=new self.PrsMotionTracker(self.jsfeat);
let generation=0;
let epoch=null;
/** Functionality: Process one generation-bound image or detection seed. Inputs: message.
 * Outputs: estimates and work duration. Logic: clear old tracking epochs before a new seed.
 * Constraints: runtime errors are visible and cause the page to terminate this worker. */
self.onmessage=event=>{
  const message=event.data, began=performance.now();
  if(message.type==='reset'){generation=message.generation;epoch=null;tracker.reset();return;}
  if(message.generation!==generation) return;
  try {
    if(message.type==='seed'&&message.epoch!==epoch){
      epoch=message.epoch;tracker.tracks=[];tracker.index=-1;tracker.sourceAt=null;
    }
    const result=message.type==='frame'?tracker.frame(message):tracker.seed(message);
    self.postMessage({type:'motion',generation,epoch,ack:message.type,result,workMs:performance.now()-began});
  } catch(error) {tracker.reset();self.postMessage({type:'error',generation,ack:message.type,error_type:error.name});}
};
