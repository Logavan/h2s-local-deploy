'use client';

import { useEffect, useState } from 'react';
import { format } from 'date-fns';

// Fix 1: Custom parser for DD/MM/YYYY HH:mm:ss and ISO format
function parseGMTString(gmtStr: string): Date {
  try {
    // Attempt to parse as ISO format first.
    let processedGmtStr = gmtStr;
    if (gmtStr.includes('T')) {
      // Check if the ISO string explicitly contains a timezone offset or 'Z'
      const hasExplicitTimezone = /[+-]\d{2}(:\d{2})?$|Z$/i.test(gmtStr);
      if (!hasExplicitTimezone) {
        // If no explicit timezone, assume it's UTC and append 'Z'
        processedGmtStr = gmtStr + 'Z';
      }
    }

    const parsedDate = new Date(processedGmtStr);
    if (!isNaN(parsedDate.getTime())) {
      return parsedDate;
    }

    // If not a valid ISO date, try manual parsing for DD/MM/YYYY HH:mm:ss
    const parts = gmtStr.split(',');
    if (parts.length === 2) {
      const [datePart, timePart] = parts.map(s => s.trim());
      const [day, month, year] = datePart.split('/').map(Number);
      const [hour, minute, second] = timePart.split(':').map(Number);

      // Create date in UTC properly
      const utcDate = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
      return utcDate;
    }

    // If neither format matches, return invalid date
    console.error("parseGMTString: Unrecognized date string format:", gmtStr);
    return new Date(''); // invalid date fallback
  } catch (err) {
    console.error("parseGMTString: Error parsing date string:", gmtStr, err);
    return new Date(''); // invalid date fallback
  }
}

export default function LocalTime({ gmt }: { gmt: string }) {
  const [localTime, setLocalTime] = useState('');

  useEffect(() => {
    // console.log("LocalTime component received gmtString:", gmt);
    const utcDate = parseGMTString(gmt);
    // console.log("LocalTime: Parsed UTC Date (ISO):", utcDate.toISOString());
    // console.log("LocalTime: Parsed UTC Date (Local String):", utcDate.toString());
    
    if (isNaN(utcDate.getTime())) {
      setLocalTime('Invalid Date');
    } else {
      const formatted = format(utcDate, 'dd/MM/yyyy hh:mm a');
      setLocalTime(formatted);
    }
  }, [gmt]);

  return <>{localTime}</>;
}
