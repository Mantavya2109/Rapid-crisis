import mongoose from "mongoose";

const deviceSchema = new mongoose.Schema({
  deviceId: {
    type: String,
    required: true,
  },
  nodeId: {
    type: String,
    required: true,
  },
});

const Device = mongoose.models.Device || mongoose.model("Device", deviceSchema);

export default Device;
