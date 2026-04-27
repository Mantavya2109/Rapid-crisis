import dotenv from "dotenv";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

import { cert, getApps, initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";

dotenv.config();

let serviceAccount;

const googleCreds = process.env.GOOGLE_APPLICATION_CREDENTIALS;

if (process.env.FIREBASE_SERVICE_ACCOUNT_JSON) {
	try {
		serviceAccount = JSON.parse(process.env.FIREBASE_SERVICE_ACCOUNT_JSON);
	} catch (e) {
		throw new Error("Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON environment variable.");
	}
} else if (googleCreds && googleCreds.trim().startsWith("{")) {
	try {
		serviceAccount = JSON.parse(googleCreds);
	} catch (e) {
		throw new Error("Failed to parse GOOGLE_APPLICATION_CREDENTIALS as JSON.");
	}
} else {
	const defaultServiceAccountPath = fileURLToPath(
		new URL("./serviceAccountKey.json", import.meta.url)
	);

	const serviceAccountPath =
		process.env.FIREBASE_SERVICE_ACCOUNT_PATH ||
		googleCreds ||
		defaultServiceAccountPath;

	if (!fs.existsSync(serviceAccountPath)) {
		throw new Error(
			`Firebase service account JSON not found at: ${serviceAccountPath}. ` +
				"Set FIREBASE_SERVICE_ACCOUNT_JSON in your environment variables, " +
				"or place serviceAccountKey.json in backend/config."
		);
	}
	serviceAccount = JSON.parse(fs.readFileSync(serviceAccountPath, "utf8"));
}

const app = getApps().length
	? getApps()[0]
	: initializeApp({
			credential: cert(serviceAccount),
		});

export const db = getFirestore(app);
export default db;
