export class WebhookGuard {
  private readonly delivered = new Set<string>();

  accept(deliveryId: string, handle: () => void): boolean {
    if (this.delivered.has(deliveryId)) {
      return false;
    }
    this.delivered.add(deliveryId);
    handle();
    return true;
  }
}
