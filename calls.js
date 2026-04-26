require('dotenv').config();
const express = require('express');
const router = express.Router();
const twilio = require('twilio');
const OpenAI = require('openai');
const ExcelJS = require('exceljs');
const path = require('path');
const fs = require('fs');

const client = new twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN);
const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

// FIXED: strip trailing slash from BASE_URL to prevent double-slash in webhook URLs
const BASE_URL = (process.env.BASE_URL || '').replace(/\/$/, '');

const callStore = {};

// ── TRIGGER CALLS ──────────────────────────────────────────────────────────
router.post('/trigger', async (req, res) => {
  const { students } = req.body;
  if (!students || students.length === 0)
    return res.json({ success: true, message: 'No absent students' });

  const results = [];
  for (const student of students) {
    try {
      const call = await client.calls.create({
        to: student.phone,
        from: process.env.TWILIO_PHONE_NUMBER,
        url: `${BASE_URL}/api/calls/twiml?name=${encodeURIComponent(student.name)}&branch=${encodeURIComponent(student.branch)}`,
        statusCallback: `${BASE_URL}/api/calls/status`,
        statusCallbackMethod: 'POST',
        machineDetection: 'Enable',
      });
      callStore[call.sid] = { ...student, callSid: call.sid };
      results.push({ name: student.name, callSid: call.sid, status: 'initiated' });
      console.log(`📞 Calling ${student.name} (${student.phone})`);
      await new Promise(r => setTimeout(r, 1500));
    } catch (err) {
      console.error(`❌ Failed ${student.name}:`, err.message);
      results.push({ name: student.name, status: 'failed', error: err.message });
    }
  }
  res.json({ success: true, results });
});

// ── TWIML — Nabot speaks to parent ────────────────────────────────────────
router.all('/twiml', (req, res) => {
  const name   = req.query.name   || 'student';
  const branch = req.query.branch || 'class';

  const twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Pause length="1"/>
  <Say voice="Polly.Aditi" language="hi-IN">
    Namaste. Main NABOT bol raha hoon, ek automated attendance system.
    Aapka student ${name}, jo ${branch} mein padhta hai,
    aaj class mein absent tha.
    Kripya beep ke baad, absence ka karan batayein.
    Aap Hindi ya English mein bol sakte hain.
  </Say>
  <Record
    action="${BASE_URL}/api/calls/response?name=..."
    maxLength="30"
    timeout="4"
    playBeep="true"
    transcribe="false"
  />
  <Hangup/>
</Response>`;

  res.type('text/xml').send(twiml);
});

// ── RESPONSE — receive recording, transcribe, reply ───────────────────────
router.post('/response', async (req, res) => {
  const name   = req.query.name   || 'student';
  const branch = req.query.branch || 'class';
  const { CallSid, RecordingUrl } = req.body;

  console.log(`🎙 Recording received for ${name}`);

  let reason    = 'No reason provided';
  let replyText = 'Dhanyavaad. Aapka response record kar liya gaya hai. Goodbye.';

  try {
    const audioUrl  = RecordingUrl + '.mp3';
    const audioResp = await fetch(audioUrl, {
      headers: {
        Authorization: 'Basic ' + Buffer.from(
          `${process.env.TWILIO_ACCOUNT_SID}:${process.env.TWILIO_AUTH_TOKEN}`
        ).toString('base64')
      }
    });

    const audioBuffer = Buffer.from(await audioResp.arrayBuffer());
    const tmpPath     = path.join(__dirname, '../tmp_audio.mp3');
    fs.writeFileSync(tmpPath, audioBuffer);

    const transcription = await openai.audio.transcriptions.create({
      file: fs.createReadStream(tmpPath),
      model: 'whisper-1',
      language: 'hi',
    });
    const rawText = transcription.text;
    console.log(`📝 Transcribed: "${rawText}"`);
    fs.unlinkSync(tmpPath);

    const gpt = await openai.chat.completions.create({
      model: 'gpt-4o-mini',
      messages: [
        {
          role: 'system',
          content: `You are NABOT, an AI attendance assistant for an Indian coaching class.
A parent just gave a voice reason for their child's absence.
Do two things:
1. Extract and summarize the reason in 1 clean English sentence.
2. Write a short polite Hindi reply (1-2 sentences) to confirm you noted the reason.
Respond in this exact JSON format only, no extra text:
{"reason": "...", "reply": "..."}`
        },
        {
          role: 'user',
          content: `Student: ${name}, Branch: ${branch}. Parent said: "${rawText}"`
        }
      ],
      max_tokens: 150
    });

    const gptRaw = gpt.choices[0].message.content.trim();
    const parsed = JSON.parse(gptRaw.replace(/```json|```/g, '').trim());
    reason    = parsed.reason;
    replyText = parsed.reply;

    console.log(`✅ ${name} — Reason: "${reason}"`);
    console.log(`🤖 Reply: "${replyText}"`);

  } catch (err) {
    console.error('Processing error:', err.message);
  }

  await saveToExcel({
    name, branch,
    phone: req.body.Called || req.body.To || '',
    date: new Date().toLocaleDateString('en-IN'),
    reason,
    callStatus: 'completed'
  });

  const twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Aditi" language="hi-IN">${replyText}</Say>
  <Pause length="1"/>
  <Say voice="Polly.Aditi" language="hi-IN">
    ${name} ki absence note kar li gayi hai. Dhanyavaad. Shubh din.
  </Say>
</Response>`;

  res.type('text/xml').send(twiml);
});

// ── STATUS CALLBACK ────────────────────────────────────────────────────────
router.post('/status', (req, res) => {
  const { CallSid, CallStatus } = req.body;
  const student = callStore[CallSid];
  if (student) console.log(`📊 ${student.name}: ${CallStatus}`);
  res.sendStatus(200);
});

// ── SAVE TO EXCEL ──────────────────────────────────────────────────────────
async function saveToExcel(record) {
  const now     = new Date();
  const month   = now.toLocaleString('en-IN', { month: 'long', year: 'numeric' });
  const dataDir = path.join(__dirname, '../data');
  const filePath = path.join(dataDir, `NABOT_${month.replace(' ', '_')}.xlsx`);

  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir);

  const workbook = new ExcelJS.Workbook();

  if (fs.existsSync(filePath)) {
    await workbook.xlsx.readFile(filePath);
  } else {
    const sheet  = workbook.addWorksheet('Absent Log');
    const header = sheet.addRow(['Name', 'Phone', 'Branch', 'Date', 'Reason', 'Call Status']);
    header.eachCell(cell => {
      cell.font = { bold: true, color: { argb: 'FFFFFFFF' } };
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF111111' } };
    });
  }

  const sheet = workbook.getWorksheet('Absent Log');
  sheet.addRow([record.name, record.phone, record.branch, record.date, record.reason, record.callStatus]);
  await workbook.xlsx.writeFile(filePath);
  console.log(`📊 Excel updated: ${record.name}`);
}

module.exports = router;