const API = (p) => fetch(p).then(async r => {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
});

const $ = (id) => document.getElementById(id);

let state = {
  city: null,       // {id, name}
  categoryId: null,
  page: 1,
  search: ""
};

const POPULAR = ["Москва","Санкт-Петербург","Казань","Екатеринбург","Новосибирск","Нижний Новгород","Ростов-на-Дону","Краснодар"];

function saveCity(c){ localStorage.setItem("city", JSON.stringify(c)); }
function loadSavedCity(){
  try { return JSON.parse(localStorage.getItem("city") || "null"); }
  catch { return null; }
}

function openModal(){
  $("cityModal").hidden = false;
}
function closeModal(){
  $("cityModal").hidden = true;
}
$("cityBtn").onclick = openModal;
$("cityClose").onclick = closeModal;

function renderPopular(){
  $("popularCities").innerHTML = "";
  POPULAR.forEach(name => {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = name;
    b.onclick = async () => {
      await findAndSelectCity(name);
      closeModal();
    };
    $("popularCities").appendChild(b);
  });
}

function renderCitiesGrid(){
  $("citiesGrid").innerHTML = "";
  POPULAR.forEach(name => {
    const d = document.createElement("div");
    d.className = "cityTile";
    d.innerHTML = `<div><b>${name}</b></div><div class="muted">Нажмите, чтобы выбрать</div>`;
    d.onclick = async () => {
      await findAndSelectCity(name);
      window.scrollTo({top:0, behavior:"smooth"});
    };
    $("citiesGrid").appendChild(d);
  });
}

async function searchCities(){
  const q = $("cityQuery").value.trim();
  if (!q) return;
  const data = await API(`/public/geo/cities?search=${encodeURIComponent(q)}&page=1`);
  const items = data.items || [];
  const box = $("cityList");
  box.innerHTML = "";
  items.forEach(c => {
    const div = document.createElement("div");
    div.className = "item";
    div.textContent = c.name;
    div.onclick = async () => {
      await selectCity({id: String(c.id), name: c.name});
      closeModal();
    };
    box.appendChild(div);
  });
}

$("citySearchBtn").onclick = searchCities;

async function findAndSelectCity(name){
  const data = await API(`/public/geo/cities?search=${encodeURIComponent(name)}&page=1`);
  const first = (data.items || [])[0];
  if (!first) return;
  await selectCity({id: String(first.id), name: first.name});
}

function setCityUI(){
  $("cityName").textContent = state.city ? state.city.name : "Выберите город";
  $("catHint").textContent = state.city ? `Каталог для: ${state.city.name}` : "Выберите город";
}

function categoryEmoji(name){
  const n = (name || "").toLowerCase();
  if (n.includes("мол")) return "🥛";
  if (n.includes("мяс") || n.includes("колбас")) return "🥩";
  if (n.includes("овощ") || n.includes("фрукт")) return "🥦";
  if (n.includes("хлеб")) return "🍞";
  if (n.includes("слад") || n.includes("конф")) return "🍫";
  if (n.includes("коф") || n.includes("чай")) return "☕";
  if (n.includes("дет")) return "🧸";
  if (n.includes("косм") || n.includes("гиги")) return "🧴";
  return "🛒";
}

async function loadCategories(){
  if (!state.city) return;
  const cats = await API(`/public/catalog/tree?city_id=${encodeURIComponent(state.city.id)}`);
  const box = $("cats");
  box.innerHTML = "";

  // cats обычно список. Берем только верхний уровень.
  (cats || []).slice(0, 24).forEach(cat => {
    const name = cat.name || cat.title || "Категория";
    const id = cat.id;
    const tile = document.createElement("div");
    tile.className = "cat";
    tile.innerHTML = `
      <div class="cat__img">${categoryEmoji(name)}</div>
      <div class="cat__body">
        <div class="cat__name">${name}</div>
        <div class="cat__sub">Открыть товары</div>
      </div>
    `;
    tile.onclick = () => {
      state.categoryId = id;
      state.page = 1;
      loadDiscountProducts(true);
    };
    box.appendChild(tile);
  });
}

function pickPrices(p){
  // пытаемся вытащить "новую" и "старую" цену из разных возможных форматов
  let cur = null, old = null;
  if (p == null) return {cur, old};
  if (typeof p === "number") return {cur: p, old: null};
  if (typeof p === "string") return {cur: p, old: null};
  if (typeof p === "object") {
    cur = p.current ?? p.price ?? p.value ?? p.new ?? p.now ?? null;
    old = p.old ?? p.previous ?? p.was ?? null;
  }
  return {cur, old};
}

function isDiscount(cur, old){
  const toNum = (x) => {
    if (x == null) return null;
    if (typeof x === "number") return x;
    const m = String(x).replace(",", ".").match(/[\d.]+/);
    return m ? Number(m[0]) : null;
  };
  const c = toNum(cur), o = toNum(old);
  return (c != null && o != null && o > c);
}

function discountBadge(cur, old){
  const toNum = (x) => {
    if (x == null) return null;
    if (typeof x === "number") return x;
    const m = String(x).replace(",", ".").match(/[\d.]+/);
    return m ? Number(m[0]) : null;
  };
  const c = toNum(cur), o = toNum(old);
  if (c == null || o == null || o <= c) return null;
  const pct = Math.round((1 - c / o) * 100);
  return pct > 0 ? `-${pct}%` : null;
}

function fmtPrice(x){
  if (x == null) return "";
  if (typeof x === "number") return `${x} ₽`;
  return String(x);
}

async function loadDiscountProducts(reset){
  if (!state.city) return;

  $("prodHint").textContent = state.categoryId ? "Показываем товары выбранной категории" : "Выберите категорию или используйте поиск";
  $("moreBtn").hidden = false;

  const params = new URLSearchParams();
  params.set("city_id", state.city.id);
  params.set("page", String(state.page));
  if (state.categoryId) params.set("category_id", String(state.categoryId));
  if (state.search) params.set("search", state.search);

  const data = await API(`/public/catalog/products?${params.toString()}`);
  const items = data.items || [];

  // Фильтруем «со скидкой», если есть старая/новая цена
  const discounted = items.filter(it => {
    const pr = pickPrices(it.price ?? it.prices);
    return isDiscount(pr.cur, pr.old);
  });

  const grid = $("products");
  if (reset) grid.innerHTML = "";

  (discounted.length ? discounted : items.slice(0, 24)).forEach(it => {
    const name = it.name || it.title || `Товар #${it.id}`;
    const pr = pickPrices(it.price ?? it.prices);
    const badge = discountBadge(pr.cur, pr.old);

    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="card__top">
        <span class="badge">${badge || "Скидка"}</span>
        <div class="price">
          <span class="price__new">${fmtPrice(pr.cur) || "—"}</span>
          <span class="price__old">${fmtPrice(pr.old) || ""}</span>
        </div>
      </div>
      <div class="card__body">
        <div class="card__name">${name}</div>
        <div class="card__meta">id: ${it.id}</div>
      </div>
    `;
    grid.appendChild(card);
  });

  // если страниц нет — просто оставим кнопку (не критично)
}

$("searchBtn").onclick = () => {
  state.search = $("q").value.trim();
  state.page = 1;
  loadDiscountProducts(true);
};

$("moreBtn").onclick = () => {
  state.page += 1;
  loadDiscountProducts(false);
};

async function selectCity(c){
  state.city = c;
  state.categoryId = null;
  state.page = 1;
  state.search = "";
  saveCity(c);
  setCityUI();
  await loadCategories();
  await loadDiscountProducts(true);
}

async function init(){
  $("year").textContent = new Date().getFullYear();
  renderPopular();
  renderCitiesGrid();

  const saved = loadSavedCity();
  if (saved) {
    await selectCity(saved);
  } else {
    // стартовое значение — попробуем СПб, если найдется
    await findAndSelectCity("Санкт-Петербург");
  }
  setCityUI();
}

init().catch(err => {
  console.error(err);
  $("prodHint").textContent = "Ошибка загрузки данных. Проверь /public/... и логи приложения.";
});
