from dataclasses import dataclass


@dataclass
class ItemRequest:
    sku: str
    quantity: int


def create_item(request: ItemRequest, inventory: dict) -> dict:
    """Validate a new item request and add the item to inventory."""
    if not request.sku.strip():
        raise ValueError("SKU is required")
    if request.quantity < 0:
        raise ValueError("quantity cannot be negative")
    item = {"sku": request.sku.strip(), "quantity": request.quantity}
    inventory[item["sku"]] = item
    return item


def update_quantity(sku: str, delta: int, inventory: dict) -> dict:
    """Apply a stock movement and reject any result below zero."""
    item = inventory[sku]
    updated = item["quantity"] + delta
    if updated < 0:
        raise ValueError("insufficient stock")
    item["quantity"] = updated
    return item
