# Store app release notes

## Version 4.2.0

Released on 14 March. Adds offline checkout: the till keeps working without a network
for up to 72 hours and syncs sales when it reconnects.

| Change | Ticket |
|---|---|
| Offline checkout | POS-1182 |
| Faster barcode scan | POS-1190 |
| Gift card balance on receipt | POS-1204 |

## Version 4.1.3

Fixes a rounding error on multi-buy discounts (ticket POS-1167) and raises the
receipt printer timeout to 8 seconds.

```bash
store-app --migrate --to 4.2.0
```

## Version 4.1.0

Adds click-and-collect lockers. A locker reservation is held for 48 hours.
