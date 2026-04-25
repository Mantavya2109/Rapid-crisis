import dotenv from "dotenv";
dotenv.config();

import { GoogleGenAI } from "@google/genai";

let _ai = null;
let _aiApiKey = null;

let _lastApiCallAtMs = 0;

export async function analyzeEvent(event) {
  try {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) throw new Error("Missing GEMINI_API_KEY");

    if (!_ai || _aiApiKey !== apiKey) {
      _ai = new GoogleGenAI({ apiKey });
      _aiApiKey = apiKey;
    }

    const {
      temperature = null,
      smoke = null,
      node = null,
      blockedNodes = [],
    } = event ?? {};

    const prompt = `You are an emergency incident analyst.

Analyze this fire event data and respond with exactly:
1) Risk level (LOW/MEDIUM/HIGH)
2) Suggested action
3) Any anomaly

Fire event:
- temperature: ${temperature ?? "unknown"}
- smoke: ${smoke ?? "unknown"}
- node: ${node ?? "unknown"}
- blockedNodes: ${Array.isArray(blockedNodes) ? blockedNodes.join(", ") || "none" : String(blockedNodes)}

Return a concise answer in plain text.`;

    const response = await _ai.models.generateContent({
      model: "gemini-2.0-flash",
      contents: prompt,
    });

    return response.text;
  } catch (err) {
    console.error("Gemini FULL ERROR:", err);
    console.error("Gemini MESSAGE:", err?.message);
    console.error("Gemini STACK:", err?.stack);
    console.log("Using fallback analysis");
    return fallbackAnalysis(event);
  }
}

function fallbackAnalysis(event) {
  const temperature = Number(event?.temperature ?? 0);
  const smoke = Number(event?.smoke ?? 0);

  if (temperature >= 80 || smoke >= 0.7) {
    return "Risk Level: HIGH. Immediate evacuation required.";
  }

  if (temperature >= 60 || smoke >= 0.4) {
    return "Risk Level: MEDIUM. Prepare for evacuation.";
  }

  return "Risk Level: LOW. Monitor situation.";
}

export async function safeAnalyzeEvent(event) {
  const now = Date.now();
  if (now - _lastApiCallAtMs < 2000) return null;
  _lastApiCallAtMs = now;
  return analyzeEvent(event);
}
