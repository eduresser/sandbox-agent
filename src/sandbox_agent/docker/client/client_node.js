const net = require("net");

const chunks = [];
process.stdin.on("data", (c) => chunks.push(c));
process.stdin.on("end", () => {
  const payload = Buffer.concat(chunks);

  const sock = net.createConnection({ path: "/tmp/kernel.sock" }, () => {
    sock.end(payload);
  });

  const response = [];
  sock.on("data", (c) => response.push(c));
  sock.on("end", () => {
    process.stdout.write(Buffer.concat(response));
  });
  sock.on("error", (err) => {
    process.stderr.write(err.message);
    process.exit(1);
  });
});
