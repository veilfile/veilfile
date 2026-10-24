// Local time (spec 4.8): the server writes each time in UTC inside <time datetime data-local>; this writes it again in the visitor's own zone, on load
// and after each change in place. With no script, or a time it cannot read, the UTC text stays. AGENTS.md, "How shell.js works", "Behaviours".
const format = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });

function localTimes() {
  for (const time of document.querySelectorAll("time[data-local]")) {
    const moment = new Date(time.dateTime);
    if (!Number.isNaN(moment.getTime())) time.textContent = format.format(moment);
  }
}

localTimes();
document.addEventListener("veilfile:swapped", localTimes);
