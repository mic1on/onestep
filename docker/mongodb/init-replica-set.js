try {
  rs.status();
} catch (error) {
  if (error.code !== 94) { // NotYetInitialized
    throw error;
  }
  const result = rs.initiate({ _id: "rs0", members: [{ _id: 0, host: "127.0.0.1:27017" }] });
  if (!result.ok) {
    throw new Error(JSON.stringify(result));
  }
}
