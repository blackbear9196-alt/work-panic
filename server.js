const express=require("express");
const multer=require("multer");
const OpenAI=require("openai");
const fs=require("fs");
const path=require("path");
const app=express();
const upload=multer({storage:multer.memoryStorage(),limits:{fileSize:8*1024*1024}});
app.use(express.json({limit:"2mb"}));
app.use(express.static(path.join(__dirname,"public")));

const SYSTEM=`You are WORK PANIC, an assistant that turns workplace messages and screenshots into clear action.
Return ONLY valid json with this exact shape:
{
 "what_they_want":"string",
 "tasks":["string"],
 "deadline":"string",
 "priority":"HIGH|MEDIUM|NORMAL",
 "reply_professional":"string",
 "reply_short":"string",
 "reply_confident":"string",
 "notes":["string"]
}
Do not invent deadlines. If no deadline is stated, use "Not specified".
Do not make legal, HR, medical, or financial determinations. If the message contains sensitive/confidential information, summarize only what is necessary.
Keep tasks concrete and concise.`;

function client(){
 if(!process.env.OPENAI_API_KEY) return null;
 return new OpenAI({apiKey:process.env.OPENAI_API_KEY});
}

app.post("/api/analyze",upload.single("screenshot"),async(req,res)=>{
 try{
  const c=client();
  if(!c) return res.status(503).json({error:"AI is not connected yet. Add OPENAI_API_KEY to the server environment."});
  const input=[];
  const text=(req.body.message||"").trim();
  if(text) input.push({type:"input_text",text});
  if(req.file){
   const mime=req.file.mimetype||"image/jpeg";
   const b64=req.file.buffer.toString("base64");
   input.push({type:"input_image",image_url:`data:${mime};base64,${b64}`});
  }
  if(!input.length) return res.status(400).json({error:"Add a message or screenshot."});
  const response=await c.responses.create({
   model:process.env.OPENAI_MODEL||"gpt-6-luna",
   instructions:SYSTEM,
   input:[{role:"user",content:input}],
   text:{format:{type:"json_object"}}
  });
  const out=response.output_text;
  res.json(JSON.parse(out));
 }catch(e){
  console.error(e);
  res.status(500).json({error:"Analysis failed. Please try again."});
 }
});

app.get("/health",(req,res)=>res.json({ok:true,aiConnected:!!process.env.OPENAI_API_KEY}));
const port=process.env.PORT||3000;
app.listen(port,()=>console.log(`WORK PANIC running on http://localhost:${port}`));
