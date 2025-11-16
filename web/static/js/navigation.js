/**
 * Navigation menu handler
 * Manages hamburger menu dropdown functionality
 */

document.addEventListener('DOMContentLoaded', () => {
    const hamburgerBtn = document.getElementById('hamburgerBtn');
    const dropdownMenu = document.getElementById('dropdownMenu');
    const hamburgerIcon = document.querySelector('.hamburger-icon');
    
    if (!hamburgerBtn || !dropdownMenu) return;
    
    let isOpen = false;
    
    // Toggle menu
    hamburgerBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        isOpen = !isOpen;
        
        if (isOpen) {
            dropdownMenu.classList.add('open');
            hamburgerBtn.setAttribute('aria-expanded', 'true');
        } else {
            dropdownMenu.classList.remove('open');
            hamburgerBtn.setAttribute('aria-expanded', 'false');
        }
    });
    
    // Close menu when clicking outside
    document.addEventListener('click', (e) => {
        if (isOpen && !dropdownMenu.contains(e.target) && e.target !== hamburgerBtn) {
            isOpen = false;
            dropdownMenu.classList.remove('open');
            hamburgerBtn.setAttribute('aria-expanded', 'false');
        }
    });
    
    // Close menu when clicking a link
    const dropdownLinks = dropdownMenu.querySelectorAll('.dropdown-link');
    dropdownLinks.forEach(link => {
        link.addEventListener('click', () => {
            isOpen = false;
            dropdownMenu.classList.remove('open');
            hamburgerBtn.setAttribute('aria-expanded', 'false');
        });
    });
});

