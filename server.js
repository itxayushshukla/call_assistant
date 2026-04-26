require('dotenv').config();
const express = require('express');
const cors = require('cors');
const bodyParser = require('body-parser');
const path = require('path');

const app = express();

app.use(cors());
app.use(bodyParser.json());
app.use(bodyParser.urlencoded({ extended: false }));

// Serve frontend
app.use(express.static(path.join(__dirname, '../frontend')));

// ── TEST ROUTE — to confirm server is alive ──
app.get('/ping', (req, res) => res.send('NABOT alive'));

// ── API ROUTES ──
app.use('/api/attendance', require('./routes/attendance'));
app.use('/api/calls',      require('./routes/calls'));

// ── ROOT ──
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, '../frontend/index.html')); // FIXED: was nabot-dashboard.html
});

// Start cron
require('./cron');

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`✅ NABOT running on http://localhost:${PORT}`);
  console.log(`✅ Routes mounted: /api/attendance, /api/calls`);
});