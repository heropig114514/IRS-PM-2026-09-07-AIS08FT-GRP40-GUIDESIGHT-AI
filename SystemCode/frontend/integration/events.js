/** Responsibilities: Provide an in-memory event stream for independent integration modules.
 * Implementation: Explicit subscription and synchronous publication with bounded caller-owned state.
 * Related Modules: guidance.js, local-mvp.js; existing camera/output controllers expose their own events.
 * Declaration Index: EventBus; EventBus.constructor; EventBus.subscribe; EventBus.publish.
 * Variable Index: listeners holds active subscriptions only; no image/text archive or global singleton.
 */

/** Functionality: Publish integration state without polling the DOM.
 * Logic: listeners share event identity and may unsubscribe; publication snapshots the set.
 * Constraints: failures propagate to the owner for diagnosis; no hidden retries or persistence. */
export class EventBus {
  /** Functionality: Initialize listeners. Inputs: None. Outputs: instance.
   * Logic: empty Set; Constraints: no browser, network or device access. */
  constructor() { this.listeners = new Set(); }
  /** Functionality: Register a listener. Inputs: function. Outputs: unsubscribe callback.
   * Logic: Set deduplicates identity; Constraints: subscriptions do not replay old state. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /** Functionality: Deliver one event. Inputs: object. Outputs: listener calls.
   * Logic: snapshot handles unsubscription during delivery; Constraints: no queuing or retention. */
  publish(event) { for (const listener of [...this.listeners]) listener(event); }
}
