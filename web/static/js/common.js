// Common functionality shared across all pages

// Load bot info on page load
async function loadBotInfo() {
    try {
        const response = await fetch('/api/bot/info');
        if (response.ok) {
            const botInfo = await response.json();
            
            // Update footer elements if they exist
            const botNameFooter = document.getElementById('botNameFooter');
            const botVersion = document.getElementById('botVersion');
            const addBotLink = document.getElementById('addBotLink');
            
            if (botNameFooter && botInfo.name) {
                botNameFooter.textContent = botInfo.name;
            }
            
            if (botVersion && botInfo.version) {
                botVersion.textContent = `v${botInfo.version}`;
            }
            
            if (addBotLink && botInfo.invite_link) {
                addBotLink.href = botInfo.invite_link;
            }
            
            // Update home page specific elements if they exist
            const botName = document.getElementById('botName');
            const botDescription = document.getElementById('botDescription');
            
            if (botName && botInfo.name) {
                botName.textContent = botInfo.name;
            }
            
            if (botDescription && botInfo.description) {
                botDescription.textContent = botInfo.description;
            }
        }
    } catch (error) {
        console.error('Failed to load bot info:', error);
    }
}

// Load bot info when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadBotInfo);
} else {
    loadBotInfo();
}
