// GitHub Actions cron is UTC. These strings are the source of truth for which
// scheduled research runs are US. Anything else, including the Taiwan retries,
// stays TW. Backup times are still Tue–Sat morning in Asia/Taipei:
//   17 22 * * 1-5 = 06:17
//   0 23 * * 1-5  = 07:00
//   20 23 * * 1-5 = 07:20
export const US_RESEARCH_CRONS = [
  '17 22 * * 1-5',
  '0 23 * * 1-5',
  '20 23 * * 1-5',
];

export const TW_RESEARCH_CRONS = [
  '30 8 * * 1-5',
  '30 9 * * 1-5',
  '30 10 * * 1-5',
  '27 13 * * 1-5',
];

export function marketForResearchSchedule(schedule) {
  return US_RESEARCH_CRONS.includes(schedule) ? 'US' : 'TW';
}
