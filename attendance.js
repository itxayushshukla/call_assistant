const express = require('express');
const router = express.Router();

let attendanceData = {}; // { date: { studentId: { name, phone, branch, status } } }

// Save attendance from dashboard
router.post('/save', (req, res) => {
  const { date, students } = req.body;
  attendanceData[date] = students;
  console.log(`📋 Attendance saved for ${date} — ${students.length} students`);
  res.json({ success: true, count: students.length });
});

// Get absent list for a date
router.get('/absent/:date', (req, res) => {
  const data = attendanceData[req.params.date] || [];
  const absent = data.filter(s => s.status === 'absent');
  res.json(absent);
});

module.exports = router;
module.exports.attendanceData = attendanceData;