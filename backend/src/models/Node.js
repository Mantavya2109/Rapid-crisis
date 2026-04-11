import mongoose from "mongoose";

const nodeSchema = new mongoose.Schema({
  id: {
    type: String,
    required: true,
    unique: true,
  },
  type: {
    type: String,
    required: true,
    enum: ["room", "corridor", "exit"],
  },
  floor: {
    type: Number,
    required: true,
  },
  x: {
    type: Number,
    required: true,
  },
  y: {
    type: Number,
    required: true,
  },
});

const Node = mongoose.models.Node || mongoose.model("Node", nodeSchema);

export default Node;
