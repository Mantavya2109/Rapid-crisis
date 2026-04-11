import express from "express";
import { handleFireAlert } from "../controllers/fireController.js";

const router = express.Router();

// POST /api/fire
router.post("/fire-alert", handleFireAlert);

export default router;
