/**
 * preload.js — Electron Preload Script
 *
 * Runs in the renderer context but has access to Node APIs via contextBridge.
 * Exposes a safe, limited API to the web dashboard.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // App info
  getVersion: () => ipcRenderer.invoke('app:version'),

  // Quit the app (from the UI if needed)
  quit: () => ipcRenderer.invoke('app:quit'),

  // Platform info — lets the UI adapt for desktop vs browser
  platform: process.platform,
  isElectron: true,
});
