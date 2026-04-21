import dotenv from "dotenv";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

import { cert, getApps, initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";

dotenv.config();

const defaultServiceAccountPath = fileURLToPath(
	new URL("./serviceAccountKey.json", import.meta.url)
);

const serviceAccountPath =
	process.env.FIREBASE_SERVICE_ACCOUNT_PATH ||
	process.env.GOOGLE_APPLICATION_CREDENTIALS ||
	defaultServiceAccountPath;

if (!fs.existsSync(serviceAccountPath)) {
	throw new Error(
		`Firebase service account JSON not found at: ${serviceAccountPath}. ` +
			"Set FIREBASE_SERVICE_ACCOUNT_PATH (or GOOGLE_APPLICATION_CREDENTIALS) " +
			"or place serviceAccountKey.json in backend/config."
	);
}

const serviceAccount = JSON.parse(fs.readFileSync(serviceAccountPath, "utf8"));

const app = getApps().length
	? getApps()[0]
	: initializeApp({
			credential: cert(serviceAccount),
		});

export const db = getFirestore(app);
export default db;
