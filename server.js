const express = require("express");
const multer = require("multer");
const OpenAI = require("openai");

const app = express();
const upload = multer({ storage: multer.memoryStorage() });

app.use(express.json({ limit: "10mb" }));
app.use(express.urlencoded({ extended: true }));
app.use(express.static("public"));

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY
});

app.get("/api/config", (req, res) => {
  res.json({
    supabaseUrl: "https://ooaikveptwczdursnpvx.supabase.co",
    supabasePublishableKey: process.env.SUPABASE_PUBLISHABLE_KEY || ""
  });
});

app.post("/api/analyze", upload.single("screenshot"), async (req, res) => {
  try {
    const text = (req.body.text || "").trim();
    const file = req.file;

    if (!text && !file) {
      return res.status(400).json({ error: "Please paste a message or upload a screenshot." });
    }

    const schema = {
      type: "object",
      properties: {
        whatTheyWant: { type: "string" },
        tasks: { type: "array", items: { type: "string" } },
        deadline: { type: "string" },
        priority: { type: "string", enum: ["Low", "Medium", "High", "Urgent"] },
        professionalReply: { type: "string" }
      },
      required: ["whatTheyWant", "tasks", "deadline", "priority", "professionalReply"],
      additionalProperties: false
    };

    const systemText = `You are WORK PANIC, an assistant that turns workplace messages into clear action.
Analyze only the user's provided workplace message or screenshot.
Identify what the sender wants, concrete tasks, deadline, priority, and a professional reply.
Do not invent facts. If a deadline is not stated, say "Not specified".
Return the result as JSON.`;

    let content;

    if (file) {
      const mime = file.mimetype || "image/png";
      const b64 = file.buffer.toString("base64");
      content = [
        { type: "input_text", text: "Analyze the workplace message shown in this screenshot only. Return the result as JSON." },
        { type: "input_image", image_url: `data:${mime};base64,${b64}` }
      ];
    } else {
      content = [
        { type: "input_text", text: `${text}\n\nReturn the result as JSON.` }
      ];
    }

    const response = await openai.responses.create({
      model: process.env.OPENAI_MODEL || "gpt-6-luna",
      input: [
        { role: "system", content: [{ type: "input_text", text: systemText }] },
        { role: "user", content }
      ],
      text: {
        format: {
          type: "json_schema",
          name: "work_panic_analysis",
          strict: true,
          schema
        }
      }
    });

    const result = JSON.parse(response.output_text);
    res.json(result);
  } catch (err) {
    console.error(err);
    res.status(500).json({
      error: err?.message || "Analysis failed."
    });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`WORK PANIC running on port ${PORT}`));
