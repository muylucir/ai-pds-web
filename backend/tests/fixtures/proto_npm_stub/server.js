const http = require("http");
http.createServer((req, res) => res.end("stub ok"))
    .listen(process.env.PORT, "127.0.0.1");
