import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import fireRoutes from "./routes/fireRoutes.js";
import pathRoutes from "./routes/pathRoutes.js";
import buildingRoutes from "./routes/buildingRoutes.js";
import db from "../config/firebase.js";

dotenv.config();

const app = express();

app.use(cors());
app.use(express.json());

// routes
app.get("/", (req, res) => {
  res.send("Server is running...");
});

app.get("/api/test", (req, res) => {
  res.json({ message: "Test route working" });
});

// router middleware
app.use("/api/fire", fireRoutes);  // Piyush
app.use("/api/path", pathRoutes);  // user guidance
app.use("/api/building", buildingRoutes);  // Naman

const PORT = process.env.PORT || 5000;

const startServer = async () => {
  app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
    console.log("Firestore instance:", typeof db);
  });
};

startServer();
