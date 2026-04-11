import mongoose from "mongoose";

const edgeSchema = new mongoose.Schema({
  from: {
    type: String,
    required: true,
  },
  to: {
    type: String,
    required: true,
  },
  distance: {
    type: Number,
    required: true,
  },
});

const Edge = mongoose.models.Edge || mongoose.model("Edge", edgeSchema);

export default Edge;
