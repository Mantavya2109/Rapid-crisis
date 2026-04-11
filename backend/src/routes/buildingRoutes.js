import express from "express";
import { saveBuildingAndFindPath } from "../controllers/buildingController.js";

const router = express.Router();

router.post("/setup-building", saveBuildingAndFindPath);

export default router;
