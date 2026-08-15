const search=document.querySelector('#docsSearch');
const sections=[...document.querySelectorAll('[data-doc-section]')];
const links=[...document.querySelectorAll('#docsNav a')];

function filterGuide(){
  const query=(search.value||'').trim().toLowerCase();
  sections.forEach(section=>section.classList.toggle('is-hidden',Boolean(query)&&!section.textContent.toLowerCase().includes(query)));
}

search.addEventListener('input',filterGuide);
const observer=new IntersectionObserver(entries=>entries.forEach(entry=>{
  if(!entry.isIntersecting)return;
  links.forEach(link=>link.classList.toggle('active',link.getAttribute('href')===`#${entry.target.id}`));
}),{rootMargin:'-20% 0px -65%'});
sections.forEach(section=>observer.observe(section));
