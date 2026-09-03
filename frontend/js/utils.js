/* ================================================================
   SunnyClass AI — Shared Utilities
   Helper functions used across all pages
   ================================================================ */

const Utils = (() => {
  'use strict';

  /* --- Theme Management --- */
  function initTheme() {
    const saved = localStorage.getItem('sc-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    return saved;
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('sc-theme', next);
    document.dispatchEvent(new CustomEvent('themechange', { detail: next }));
    return next;
  }

  function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  /* --- Scroll Progress --- */
  function initScrollProgress() {
    function update() {
      const h = document.documentElement.scrollHeight - window.innerHeight;
      const p = h > 0 ? window.scrollY / h : 0;
      document.documentElement.style.setProperty('--scroll-progress', p.toFixed(4));
    }
    window.addEventListener('scroll', update, { passive: true });
    update();
  }

  /* --- IntersectionObserver Reveal --- */
  function initReveal(selector = '.reveal') {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
    );
    document.querySelectorAll(selector).forEach((el) => observer.observe(el));
    return observer;
  }

  /* --- Toast Notifications --- */
  let toastContainer = null;

  function showToast(message, type = 'info', duration = 4000) {
    if (!toastContainer) {
      toastContainer = document.createElement('div');
      toastContainer.className = 'toast-container';
      document.body.appendChild(toastContainer);
    }

    const icons = {
      success: '✓',
      error: '✕',
      warning: '⚠',
      info: 'ℹ',
    };

    const toast = document.createElement('div');
    toast.className = `toast toast-${type} glass`;
    toast.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span>${message}</span>`;
    toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(30px)';
      toast.style.transition = 'all 0.3s var(--ease-out)';
      setTimeout(() => toast.remove(), 300);
    }, duration);

    return toast;
  }

  /* --- Simple Tab System --- */
  function initTabs(container) {
    const tabsEl =
      typeof container === 'string'
        ? document.querySelector(container)
        : container;
    if (!tabsEl) return;

    const buttons = tabsEl.querySelectorAll('.tab');
    const panelContainer =
      tabsEl.closest('[data-tab-group]') || tabsEl.parentElement;
    const panels = panelContainer.querySelectorAll('.tab-panel');

    buttons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.tab;
        buttons.forEach((b) => b.classList.remove('active'));
        panels.forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        const panel = panelContainer.querySelector(`[data-panel="${target}"]`);
        if (panel) panel.classList.add('active');
      });
    });
  }

  /* --- Modal --- */
  function openModal(id) {
    const overlay = document.getElementById(id);
    if (overlay) {
      overlay.classList.add('open');
      document.body.style.overflow = 'hidden';
    }
  }

  function closeModal(id) {
    const overlay = document.getElementById(id);
    if (overlay) {
      overlay.classList.remove('open');
      document.body.style.overflow = '';
    }
  }

  /* --- Local Storage Helpers --- */
  function saveData(key, data) {
    try {
      localStorage.setItem(`sc-${key}`, JSON.stringify(data));
    } catch (e) {
      console.warn('LocalStorage save failed:', e);
    }
  }

  function loadData(key, fallback = null) {
    try {
      const raw = localStorage.getItem(`sc-${key}`);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;
    }
  }

  /* --- UUID Generator --- */
  function uuid() {
    return crypto.randomUUID
      ? crypto.randomUUID()
      : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
          const r = (Math.random() * 16) | 0;
          return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
        });
  }

  /* --- Date Formatting --- */
  function formatTime(date) {
    return new Date(date).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function formatDate(date) {
    return new Date(date).toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }

  function timeAgo(date) {
    const seconds = Math.floor((Date.now() - new Date(date).getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  }

  /* --- Debounce / Throttle --- */
  function debounce(fn, delay = 250) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), delay);
    };
  }

  function throttle(fn, interval = 200) {
    let last = 0;
    return (...args) => {
      const now = Date.now();
      if (now - last >= interval) {
        last = now;
        fn(...args);
      }
    };
  }

  // API calls should go through window.API (see js/api.js) which handles
  // JWT auth headers, 401 redirects, request timeouts, and ApiError correctly.

  /* --- Mobile Navigation Toggle --- */
  function initMobileNav() {
    const toggle = document.querySelector('.nav-toggle');
    const links = document.querySelector('.nav-links');
    if (!toggle || !links) return;

    toggle.addEventListener('click', () => {
      links.classList.toggle('open');
      toggle.setAttribute('aria-expanded', links.classList.contains('open'));
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.nav')) {
        links.classList.remove('open');
      }
    });
  }

  /* --- Generate Initials from Name --- */
  function initials(name) {
    return name
      .split(' ')
      .map((w) => w[0])
      .join('')
      .toUpperCase()
      .slice(0, 2);
  }

  /* --- Engagement Ring SVG Generator --- */
  function createEngagementRing(container, value = 0) {
    const el =
      typeof container === 'string'
        ? document.querySelector(container)
        : container;
    if (!el) return;

    const radius = 52;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference * (1 - value / 100);

    el.classList.add('engagement-ring');
    el.innerHTML = `
      <svg width="120" height="120" viewBox="0 0 120 120">
        <circle class="bg" cx="60" cy="60" r="${radius}" />
        <circle class="progress" cx="60" cy="60" r="${radius}"
          stroke-dasharray="${circumference}"
          stroke-dashoffset="${offset}" />
      </svg>
      <div class="engagement-value">${Math.round(value)}%</div>
    `;

    return {
      update(newVal) {
        const circle = el.querySelector('.progress');
        const label = el.querySelector('.engagement-value');
        circle.style.strokeDashoffset = circumference * (1 - newVal / 100);
        label.textContent = `${Math.round(newVal)}%`;
      },
    };
  }

  /* --- Public API --- */
  return {
    initTheme,
    toggleTheme,
    getTheme,
    initScrollProgress,
    initReveal,
    showToast,
    initTabs,
    openModal,
    closeModal,
    saveData,
    loadData,
    uuid,
    formatTime,
    formatDate,
    timeAgo,
    debounce,
    throttle,
    initMobileNav,
    initials,
    createEngagementRing,
  };
})();

// Auto-init theme on load
Utils.initTheme();
