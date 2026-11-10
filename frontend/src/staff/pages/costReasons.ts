// The reasons the cost canvas gives are fixed sentences (an enum in the schema). Each known one maps to a key in the language files;
// one the console does not know yet is shown as the backend sent it, so nothing is hidden.
export const COST_REASON_KEYS: Readonly<Record<string, string>> = {
  "fewer than seven days of measured traffic": "fewerThanSevenDays",
  "no completed workflows": "noCompleted",
  "platform floor (P) is not configured": "floorNotSet",
  "reviewer rates are not configured": "ratesNotSet",
  "a reviewer role has no configured rate": "roleNoRate",
  "reviewer minutes are not measured yet": "minutesNotMeasured",
  "retry cost is not measured yet": "retryNotMeasured",
};
