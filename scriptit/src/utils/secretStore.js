const Store = require('electron-store');

const store = new Store({
  name: 'scriptit-config',
  encryptionKey: 'scriptit-local-encryption-v1',
});

function getApiKey() {
  return store.get('apiKey');
}

function setApiKey(key) {
  store.set('apiKey', key);
}

function clearApiKey() {
  store.delete('apiKey');
}

module.exports = { getApiKey, setApiKey, clearApiKey };
