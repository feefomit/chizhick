let cityId = null;
let categoryId = null;
let page = 1;

const el = (id) => document.getElementById(id);

function renderList(container, items, onClick) {
  container.innerHTML = "";
  items.forEach((x) => {
    const div = document.createElement("div");
    div.className = "item";
    div.textContent = x.name ?? x.title ?? JSON.stringify(x);
    div.onclick = () => onClick(x);
    container.appendChild(div);
  });
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

async function loadOffers() {
  const data = await api("/public/offers/active");
  // в quick start это объект с title/description... :contentReference[oaicite:2]{index=2}
  el("offersBox").innerHTML = `
    <div><b>${data.title ?? "Акции"}</b></div>
    <div class="muted">${(data.description ?? "").replaceAll("\r\n","<br>")}</div>
  `;
}

async function searchCities() {
  const q = el("cityQuery").value.trim();
  if (!q) return;
  const data = await api(`/public/geo/cities?search=${encodeURIComponent(q)}&page=1`);
  renderList(el("cityList"), data.items ?? [], (c) => selectCity(c));
}

async function selectCity(c) {
  cityId = String(c.id);
  el("selectedCity").textContent = `Выбран: ${c.name} (city_id=${cityId})`;
  await loadCategories();
  await loadProducts(true);
}

async function loadCategories() {
  const cats = await api(`/public/catalog/tree?city_id=${encodeURIComponent(cityId)}`);
  // дерево — список словарей, есть id/name :contentReference[oaicite:3]{index=3}
  renderList(el("catList"), cats ?? [], (cat) => {
    categoryId = cat.id;
    page = 1;
    loadProducts(true);
  });
}

function pickPrice(p) {
  // структура цены может отличаться — сделаем безопасно
  if (p == null) return "";
  if (typeof p === "number") return `${p} ₽`;
  if (typeof p === "string") return p;
  if (typeof p === "object") return p.value ? `${p.value} ₽` : (p.current ?? p.price ?? "");
  return "";
}

async function loadProducts(reset = false) {
  if (!cityId) return;
  const q = el("searchQuery").value.trim();
  const params = new URLSearchParams();
  params.set("city_id", cityId);
  params.set("page", String(page));
  if (categoryId) params.set("category_id", String(categoryId));
  if (q) params.set("search", q);

  const data = await api(`/public/catalog/products?${params.toString()}`);
  const items = data.items ?? [];
  el("pageInfo").textContent = `Стр. ${data.previous ? data.previous + 1 : 1} / ${data.total_pages ?? "?"} (всего: ${data.count ?? "?"})`;

  const grid = el("products");
  if (reset) grid.innerHTML = "";

  items.forEach((it) => {
    const card = document.createElement("div");
    card.className = "card";
    const name = it.name ?? it.title ?? `ID ${it.id}`;
    const price = pickPrice(it.price ?? it.prices);
    card.innerHTML = `
      <div><b>${name}</b></div>
      <div class="muted">id: ${it.id}</div>
      <div style="margin-top:8px">${price || "<span class='muted'>цена в данных не найдена</span>"}</div>
    `;
    grid.appendChild(card);
  });
}

el("citySearch").onclick = () => searchCities();
el("doSearch").onclick = () => { page = 1; loadProducts(true); };

loadOffers().catch(console.error);
