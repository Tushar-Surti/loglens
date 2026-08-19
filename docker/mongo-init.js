// Promote the standalone mongod to a single-node replica set.
//
// Change streams and multi-document transactions require a replica set, and the
// platform uses both patterns; a single node keeps the local footprint small
// while behaving like production.
try {
  const status = rs.status();
  print("replica set already initialised: " + status.set);
} catch (error) {
  print("initialising replica set rs0…");
  rs.initiate({
    _id: "rs0",
    members: [{ _id: 0, host: "mongo:27017", priority: 1 }],
  });

  // Wait until this node has actually become PRIMARY before returning, so
  // dependent containers do not race the election.
  let attempts = 0;
  while (attempts < 60) {
    try {
      if (db.hello().isWritablePrimary) {
        print("replica set is primary after " + attempts + " checks");
        break;
      }
    } catch (e) {
      // election in progress
    }
    sleep(500);
    attempts += 1;
  }
}

db = db.getSiblingDB("loglens");
db.config.updateOne(
  { key: "bootstrap" },
  { $set: { key: "bootstrap", value: { initialised_at: new Date() } } },
  { upsert: true }
);
print("loglens database ready");
