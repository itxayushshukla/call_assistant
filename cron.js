const cron = require('node-cron');
const fetch = require('node-fetch'); // npm install node-fetch@2

// Runs every day at 7:00 PM
cron.schedule('0 19 * * *', async () => {
  console.log('⏰ 7PM — Auto-triggering absent calls...');
  const today = new Date().toLocaleDateString('en-IN');

  try {
    // Get today's absent list
    const res = await fetch(`http://localhost:${process.env.PORT||3000}/api/attendance/absent/${today}`);
    const absentStudents = await res.json();

    if (absentStudents.length === 0) {
      console.log('✅ No absent students today.');
      return;
    }

    // Trigger calls
    await fetch(`http://localhost:${process.env.PORT||3000}/api/calls/trigger`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ students: absentStudents })
    });

    console.log(`📞 Calls triggered for ${absentStudents.length} students`);
  } catch (err) {
    console.error('Cron error:', err.message);
  }
}, { timezone: 'Asia/Kolkata' });

console.log('✅ Cron scheduler armed — calls fire at 7PM IST daily');