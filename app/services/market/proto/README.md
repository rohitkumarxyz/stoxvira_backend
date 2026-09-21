# Upstox market feed protobuf

`MarketDataFeedV3.proto` is Upstox's schema, downloaded from:

    https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto

`MarketDataFeedV3_pb2.py` is generated from it and committed so the app does
not need a protobuf compiler at runtime. Regenerate after replacing the
`.proto`:

```bash
python -m grpc_tools.protoc -I app/services/market/proto \
  --python_out=app/services/market/proto \
  app/services/market/proto/MarketDataFeedV3.proto
```

`grpcio-tools` is a dev dependency only — nothing imports it at runtime.
