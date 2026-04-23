/*
    Sample data for testing
*/

import dotenv from "dotenv";
import mongoose from "mongoose";
import { connectDB } from "../config/db.js";
import Node from "../models/Node.js";
import Edge from "../models/Edge.js";
import path from "path";

dotenv.config({ path: path.resolve("../../.env") });

const seedData = async () => {
  await connectDB();

  // Clear old data (useful for quick testing)
  await Node.deleteMany({});
  await Edge.deleteMany({});

  // Minimal graph: rooms + corridors + one EXIT
  const nodes = [
    { id: "ROOM_1", type: "room", floor: 1, x: 10, y: 10 },
    { id: "ROOM_2", type: "room", floor: 1, x: 30, y: 10 },
    { id: "CORRIDOR_1", type: "corridor", floor: 1, x: 20, y: 20 },
    { id: "EXIT_1", type: "exit", floor: 1, x: 20, y: 40 },
  ];

  const edges = [
    { from: "ROOM_1", to: "CORRIDOR_1", distance: 5 },
    { from: "ROOM_2", to: "CORRIDOR_1", distance: 5 },
    { from: "CORRIDOR_1", to: "EXIT_1", distance: 10 },
  ];

  await Node.insertMany(nodes);
  await Edge.insertMany(edges);

  console.log("Seed complete:");
  console.log(`- Nodes inserted: ${nodes.length}`);
  console.log(`- Edges inserted: ${edges.length}`);

  await mongoose.connection.close();
};

seedData()
  .then(() => process.exit(0))
  .catch(async (error) => {
    console.error("Seed failed:", error.message);
    try {
      await mongoose.connection.close();
    } catch {
      console.log("MONGO_URI:", process.env.MONGO_URI);
    }
    process.exit(1);
  });
