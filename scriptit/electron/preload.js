const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('scriptit', {
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setApiKey: (key) => ipcRenderer.invoke('settings:setApiKey', key),
  clearApiKey: () => ipcRenderer.invoke('settings:clearApiKey'),
  generate: (payload) => ipcRenderer.invoke('script:generate', payload),
  getResearch: (jobId) => ipcRenderer.invoke('research:get', jobId),
  startResearch: (payload) => ipcRenderer.invoke('research:start', payload),
  resumeResearch: (jobId) => ipcRenderer.invoke('research:resume', jobId),
  searchClaims: (query) => ipcRenderer.invoke('research:claims', query),
  getResearchBrief: (jobId) => ipcRenderer.invoke('research:brief', jobId),
  getEvidenceMap: (jobId) => ipcRenderer.invoke('research:evidence', jobId),
  researchHealth: () => ipcRenderer.invoke('research:health'),
  validatePublish: (payload) => ipcRenderer.invoke('script:validatePublish', payload),
  exportMarkdown: (data) => ipcRenderer.invoke('export:markdown', data),
  openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),
  classify: (topic) => ipcRenderer.invoke('classifier:classify', topic),
  narratorProfile: (payload) => ipcRenderer.invoke('narrator:profile', payload),
  extractVideoStructure: (payload) => ipcRenderer.invoke('structure:extractFromYoutube', payload),
  generateFromYoutubeReference: (payload) => ipcRenderer.invoke('script:generateFromYoutubeReference', payload),
});
