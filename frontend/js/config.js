/* ================================================================
   SunnyClass AI — deploy-time configuration
   Runs BEFORE api.js on every page. When the frontend is hosted
   separately from the backend (e.g. Vercel + Render), set the base
   URL below. Same-origin deploys (Render blueprint, docker compose,
   local dev) need no changes.
   ================================================================ */
(() => {
  'use strict';

  // ↓↓↓ REPLACE with your backend URL when using a split deploy, e.g.
  // window.SUNNYCLASS_API_BASE = 'https://sunnyclass-api.onrender.com';
  window.SUNNYCLASS_API_BASE = window.SUNNYCLASS_API_BASE || '';

  // The face models are served by the BACKEND at /models — point the
  // loader there whenever the API base is off-origin.
  if (window.SUNNYCLASS_API_BASE && !window.SUNNYCLASS_MODEL_URL) {
    window.SUNNYCLASS_MODEL_URL =
      window.SUNNYCLASS_API_BASE.replace(/\/$/, '') + '/models';
  }
})();
