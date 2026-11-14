export interface EventEnvelope {
  id: string;
  topic: string;
  payload: unknown;
}

export function validateEvent(value: unknown): EventEnvelope {
  if (typeof value !== "object" || value === null) {
    throw new Error("event must be an object");
  }
  const event = value as Partial<EventEnvelope>;
  if (typeof event.id !== "string" || event.id.length === 0) {
    throw new Error("event id is required");
  }
  if (typeof event.topic !== "string" || event.topic.length === 0) {
    throw new Error("event topic is required");
  }
  return { id: event.id, topic: event.topic, payload: event.payload };
}
