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

const SUPABASE_URL = "https://ooaikveptwczdursnpvx.supabase.co";

app.get("/api/config", (req, res) => {
  res.json({
    supabaseUrl: SUPABASE_URL,
    supabasePublishableKey: process.env.SUPABASE_PUBLISHABLE_KEY || ""
  });
});

function getBearerToken(req) {
  const header = req.headers.authorization || "";
  if (!header.startsWith("Bearer ")) return "";
  return header.slice(7);
}

async function supabaseFetch(path, options = {}, accessToken = "") {
  const headers = {
    apikey: process.env.SUPABASE_PUBLISHABLE_KEY || "",
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
    ...(options.headers || {})
  };

  return fetch(`${SUPABASE_URL}${path}`, {
    ...options,
    headers
  });
}

async function getUserFromToken(accessToken) {
  if (!accessToken) return null;

  const r = await supabaseFetch("/auth/v1/user", { method: "GET" }, accessToken);
  if (!r.ok) return null;

  return await r.json();
}

function currentMonthStart() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit"
  }).formatToParts(new Date());

  const year = parts.find(p => p.type === "year").value;
  const month = parts.find(p => p.type === "month").value;
  return `${year}-${month}-01`;
}

async function getUsage(userId, accessToken) {
  const monthStart = currentMonthStart();
  const query = `/rest/v1/usage_monthly?user_id=eq.${encodeURIComponent(userId)}&month_start=eq.${encodeURIComponent(monthStart)}&select=id,user_id,month_start,analysis_count`;
  const r = await supabaseFetch(query, { method: "GET" }, accessToken);

  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Could not read usage: ${body}`);
  }

  const rows = await r.json();
  return {
    monthStart,
    count: rows[0]?.analysis_count || 0,
    row: rows[0] || null
  };
}

async function consumeFreeAnalysis(userId, accessToken) {
  const usage = await getUsage(userId, accessToken);

  if (usage.count >= 10) {
    return { allowed: false, count: usage.count, monthStart: usage.monthStart };
  }

  if (!usage.row) {
    const insert = await supabaseFetch(
      "/rest/v1/usage_monthly",
      {
        method: "POST",
        headers: { Prefer: "return=representation" },
        body: JSON.stringify({
          user_id: userId,
          month_start: usage.monthStart,
          analysis_count: 1
        })
      },
      accessToken
    );

    if (!insert.ok) {
      // Another request may have created the monthly row first.
      const retry = await getUsage(userId, accessToken);
      if (retry.count >= 10) {
        return { allowed: false, count: retry.count, monthStart: retry.monthStart };
      }

      const update = await supabaseFetch(
        `/rest/v1/usage_monthly?id=eq.${encodeURIComponent(retry.row.id)}`,
        {
          method: "PATCH",
          headers: { Prefer: "return=representation" },
          body: JSON.stringify({ analysis_count: retry.count + 1 })
        },
        accessToken
      );

      if (!update.ok) {
        const body = await update.text();
        throw new Error(`Could not update usage: ${body}`);
      }

      return { allowed: true, count: retry.count + 1, monthStart: retry.monthStart };
    }

    return { allowed: true, count: 1, monthStart: usage.monthStart };
  }

  const newCount = usage.count + 1;
  const update = await supabaseFetch(
    `/rest/v1/usage_monthly?id=eq.${encodeURIComponent(usage.row.id)}`,
    {
      method: "PATCH",
      headers: { Prefer: "return=representation" },
      body: JSON.stringify({ analysis_count: newCount })
    },
    accessToken
  );

  if (!update.ok) {
    const body = await update.text();
    throw new Error(`Could not update usage: ${body}`);
  }

  return { allowed: true, count: newCount, monthStart: usage.monthStart };
}

async function saveHistory(userId, accessToken, inputType, inputText, result) {
  const r = await supabaseFetch(
    "/rest/v1/analysis_history",
    {
      method: "POST",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({
        user_id: userId,
        input_type: inputType,
        input_text: inputText || null,
        result
      })
    },
    accessToken
  );

  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Could not save history: ${body}`);
  }
}

app.get("/api/usage", async (req, res) => {
  try {
    const accessToken = getBearerToken(req);
    const user = await getUserFromToken(accessToken);

    if (!user) {
      return res.status(401).json({ error: "Please log in again." });
    }

    const usage = await getUsage(user.id, accessToken);
    res.json({ count: usage.count, limit: 10, monthStart: usage.monthStart });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err?.message || "Could not load usage." });
  }
});

app.get("/api/history", async (req, res) => {
  try {
    const accessToken = getBearerToken(req);
    const user = await getUserFromToken(accessToken);

    if (!user) {
      return res.status(401).json({ error: "Please log in again." });
    }

    const query = `/rest/v1/analysis_history?user_id=eq.${encodeURIComponent(user.id)}&select=id,input_type,input_text,result,created_at&order=created_at.desc&limit=25`;
    const r = await supabaseFetch(query, { method: "GET" }, accessToken);

    if (!r.ok) {
      const body = await r.text();
      throw new Error(`Could not load history: ${body}`);
    }

    res.json({ history: await r.json() });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err?.message || "Could not load history." });
  }
});

app.post("/api/analyze", upload.single("screenshot"), async (req, res) => {
  try {
    const text = (req.body.text || "").trim();
    const file = req.file;
    const accessToken = getBearerToken(req);
    const user = await getUserFromToken(accessToken);

    if (!user) {
      return res.status(401).json({ error: "Please log in again." });
    }

    if (!text && !file) {
      return res.status(400).json({ error: "Please paste a message or upload a screenshot." });
    }

    const usage = await consumeFreeAnalysis(user.id, accessToken);

    if (!usage.allowed) {
      return res.status(429).json({
        error: "You have used all 10 FREE analyses for this month.",
        code: "FREE_LIMIT_REACHED",
        used: usage.count,
        limit: 10
      });
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
    let inputType = "text";

    if (file) {
      inputType = "screenshot";
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

    await saveHistory(user.id, accessToken, inputType, text, result);

    res.json({
      ...result,
      usage: {
        used: usage.count,
        limit: 10
      }
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({
      error: err?.message || "Analysis failed."
    });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`WORK PANIC running on port ${PORT}`));
