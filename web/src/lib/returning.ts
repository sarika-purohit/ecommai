/** Set while someone is signed in (or in demo mode), so the sign-in screen can stay hidden on reload. */
export const RETURNING_KEY = "ecommai.returning";
export const RETURNING_ATTRIBUTE = "data-returning";

export const returningScript = `(function(){try{if(localStorage.getItem(${JSON.stringify(RETURNING_KEY)})==="1")document.documentElement.setAttribute(${JSON.stringify(RETURNING_ATTRIBUTE)},"")}catch(e){}})()`;
