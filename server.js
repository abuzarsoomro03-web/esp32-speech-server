import express from "express";
import { WebSocketServer } from "ws";

const app = express();
const PORT = process.env.PORT || 3000;

// Create basic HTTP server
const server = app.listen(PORT, () => {
  console.log(`🌐 HTTP + WS Server running on port ${PORT}`);
});

app.get("/", (req, res) => {
  res.send("✅ ESP32 WebSocket Speech Server is running!");
});

// Create WebSocket server
const wss = new WebSocketServer({ server });

wss.on("connection", (ws, req) => {
  console.log(`🔗 Client connected: ${req.socket.remoteAddress}`);

  ws.send(JSON.stringify({ type: "connected", message: "Welcome ESP32!" }));

  ws.on("message", (data) => {
    try {
      if (typeof data === "string" || data instanceof String) {
        console.log("[WS] Text:", data.toString());
      } else {
        console.log(`[WS] Binary: ${data.length} bytes`);
        // You can buffer or process audio here
      }
    } catch (err) {
      console.error("Error handling message:", err);
    }
  });

  ws.on("close", () => console.log("❌ Client disconnected"));
  ws.on("error", (err) => console.error("⚠️ WebSocket error:", err));
});
