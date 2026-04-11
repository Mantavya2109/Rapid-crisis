import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import { connectDB } from "./config/db.js";

// config
dotenv.config();

const app = express();

// middleware
app.use(cors());
app.use(express.json());

// routes
app.get("/", (req, res) => {
  res.send("Server is running...");
});

app.get("/api/test", (req, res) => {
  res.json({ message: "Test route working" });
});

// server
const PORT = process.env.PORT || 5000;

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
  connectDB();
});
