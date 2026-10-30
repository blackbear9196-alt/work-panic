const express = require("express");
const multer = require("multer");
const OpenAI = require("openai");
const path = require("path");

const app = express();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024 }
});

app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY
});

const SYSTEM = `You are WORK PANIC, an assistant that turns workplace messages and screenshots into clear action.

Return ONLY valid JSON with this exact shape:
{
  "what_they_want": "string",
  "tasks": ["string"],
  "deadline": "string",
  "priority": "HIGH|MEDIUM|NORMAL",
  "reply_professional": "string",
  "reply_short": "string",
  "reply_confident": "string",
  "notes": ["string"]
}

Do not invent deadlines. If no deadline is stated, use "Not specified".
Do not make legal, HR, medical, or financial determinations.
If the message contains sensitive/confidential information, summarize only what is necessary.
Keep tasks concrete and concise.`;

app.post("/api/analyze", upload.single("screenshot"), async (req, res) => {
  try {
    const text = (req.body.message || "").trim();

    if (!text && !req.file) {
      return res.status(400).json({
        error: "Add a message or screenshot."
      });
    }

    // IMPORTANT:
    // If a screenshot is uploaded, analyze ONLY the screenshot.
    // Do not combine it with any text that may still be present in the form.
    let content;

    if (req.file) {
      const mime = req.file.mimetype || "image/jpeg";
      const b64 = req.file.buffer.toString("base64");

      content = [
        {
          type: "input_image",
          image_url: `data:${mime};base64,${b64}`
        },
        {
          type: "input_text",
          text: "Analyze the workplace message shown in this screenshot only. Return the result as JSON."
        }
      ];
    } else {
      content = [
        {
          type: "input_text",
          text: `${text}\n\nReturn the result as JSON.`
        }
      ];
    }

    const response = await client.responses.create({
      model: process.env.OPENAI_MODEL || "gpt-6-luna",
      instructions: SYSTEM,
      input: [
        {
          role: "user",
          content
        }
      ],
      text: {
        format: {
          type: "json_object"
        }
      }
    });

    const raw = response.output_text || "";
    const result = JSON.parse(raw);

    return res.json(result);
  } catch (error) {
    console.error("WORK PANIC analysis error:", error);

    return res.status(500).json({
      error: "Analysis failed. Please try again."
    });
  }
});

const PORT = process.env.PORT || 10000;

app.listen(PORT, () => {
  console.log(`WORK PANIC running on http://localhost:${PORT}`);
});
