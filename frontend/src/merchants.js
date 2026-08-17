/**
 * Known merchants, so a transaction shows a real logo and a clean name
 * instead of raw bank text like "UPI/P2M/4471/SWIGGYINSTAMART". Keys match
 * src/brandIcons.js. Separate from categorizer.py's OBVIOUS_MERCHANTS
 * (category-only, e.g. "petrol" -> Transport) — this needs brand-level
 * granularity to pick the right logo, not just the right category.
 */
export const MERCHANTS = [
  { key: "swiggy", label: "Swiggy", match: ["swiggy"] },
  { key: "zomato", label: "Zomato", match: ["zomato"] },
  { key: "dominos", label: "Domino's", match: ["dominos", "domino's", "domino"] },
  { key: "starbucks", label: "Starbucks", match: ["starbucks"] },
  { key: "eatsure", label: "EatSure", match: ["eatsure"] },
  { key: "dunzo", label: "Dunzo", match: ["dunzo"] },
  { key: "blinkit", label: "Blinkit", match: ["blinkit", "grofers"] },
  { key: "zepto", label: "Zepto", match: ["zepto"] },
  { key: "bigbasket", label: "BigBasket", match: ["bigbasket", "bigbskt"] },
  { key: "dmart", label: "DMart", match: ["dmart", "avenue supermart"] },
  { key: "instamart", label: "Swiggy Instamart", match: ["instamart"] },
  { key: "jiomart", label: "JioMart", match: ["jiomart"] },
  { key: "uber", label: "Uber", match: ["uber"] },
  { key: "ola", label: "Ola", match: ["olacabs", "ola cabs", "ola money", " ola "] },
  { key: "rapido", label: "Rapido", match: ["rapido"] },
  { key: "irctc", label: "IRCTC", match: ["irctc"] },
  { key: "shell", label: "Shell", match: ["shell"] },
  { key: "hpcl", label: "HP Petrol", match: ["hpcl", "hindustan petroleum"] },
  { key: "iocl", label: "Indian Oil", match: ["iocl", "indianoil", "indian oil"] },
  { key: "bpcl", label: "Bharat Petroleum", match: ["bpcl", "bharat petroleum"] },
  { key: "fastag", label: "FASTag", match: ["fastag"] },
  { key: "redbus", label: "redBus", match: ["redbus"] },
  { key: "indigo", label: "IndiGo", match: ["indigo", "interglobe"] },
  { key: "ammayatri", label: "Namma Yatri", match: ["namma yatri"] },
  { key: "amazon", label: "Amazon", match: ["amazon", "amzn"] },
  { key: "flipkart", label: "Flipkart", match: ["flipkart", "fkrt"] },
  { key: "myntra", label: "Myntra", match: ["myntra"] },
  { key: "ajio", label: "Ajio", match: ["ajio"] },
  { key: "nykaa", label: "Nykaa", match: ["nykaa"] },
  { key: "meesho", label: "Meesho", match: ["meesho"] },
  { key: "netflix", label: "Netflix", match: ["netflix"] },
  { key: "spotify", label: "Spotify", match: ["spotify"] },
  { key: "hotstar", label: "JioHotstar", match: ["hotstar", "disney+"] },
  { key: "primevideo", label: "Prime Video", match: ["prime video", "primevideo"] },
  { key: "bookmyshow", label: "BookMyShow", match: ["bookmyshow", "bms"] },
  { key: "youtube", label: "YouTube", match: ["youtube"] },
  { key: "pharmeasy", label: "PharmEasy", match: ["pharmeasy"] },
  { key: "apollo", label: "Apollo Pharmacy", match: ["apollo"] },
  { key: "practo", label: "Practo", match: ["practo"] },
  { key: "onemg", label: "Tata 1mg", match: ["1mg", "tata 1mg"] },
  { key: "cult", label: "Cult.fit", match: ["cult.fit", "cultfit", "curefit", " cult "] },
  { key: "netmeds", label: "Netmeds", match: ["netmeds"] },
  { key: "airtel", label: "Airtel", match: ["airtel"] },
  { key: "jio", label: "Jio", match: ["reliance jio", " jio "] },
  { key: "vodafone", label: "Vi", match: ["vodafone", "vodafone idea", " vi "] },
  { key: "zerodha", label: "Zerodha", match: ["zerodha", "kite"] },
  { key: "groww", label: "Groww", match: ["groww"] },
  { key: "upstox", label: "Upstox", match: ["upstox"] },
  { key: "kuvera", label: "Kuvera", match: ["kuvera"] },
  { key: "apple", label: "Apple", match: ["apple.com", "itunes", "apple services"] },
  { key: "googlepay", label: "Google Pay", match: ["google pay", "gpay"] },
  { key: "paytm", label: "Paytm", match: ["paytm"] },
  { key: "phonepe", label: "PhonePe", match: ["phonepe"] },
  { key: "amazonpay", label: "Amazon Pay", match: ["amazon pay", "amazonpay"] },
]

/**
 * Finds a known merchant in free text (a merchant name from an SMS, or a
 * typed note). Longest match wins so "prime video" beats a shorter,
 * coincidental substring match.
 */
export function detectMerchant(text) {
  if (!text) return null
  const lower = ` ${text.toLowerCase()} `
  let best = null

  for (const merchant of MERCHANTS) {
    for (const fragment of merchant.match) {
      if (lower.includes(fragment) && (!best || fragment.length > best.length)) {
        best = { merchant, length: fragment.length }
      }
    }
  }

  return best?.merchant ?? null
}
