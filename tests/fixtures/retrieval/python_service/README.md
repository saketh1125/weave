# Stockroom API

Stockroom tracks small business inventory and helps warehouse staff adjust item
quantities through a private HTTP API. The service records each accepted stock
change so an operator can review what changed and when.

## Developer workflow

Create an item with its SKU and starting quantity, then use the quantity
endpoint when stock moves. Requests with an invalid quantity are rejected
before an inventory change is stored.
