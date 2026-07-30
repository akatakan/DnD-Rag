import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Backpack, CircleDollarSign, PackagePlus, Scale, Trash2 } from "lucide-react";
import type { Character } from "../types";

type RunCommand = (
  type: string,
  payload?: Record<string, unknown>,
) => Promise<void>;

export default function InventoryPanel({
  character,
  run,
}: {
  character: Character;
  run: RunCommand;
}) {
  const [name, setName] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [unitWeight, setUnitWeight] = useState(0);
  const inventory = character.inventory_state;
  const entries = useMemo(
    () => Object.values(inventory.entries),
    [inventory.entries],
  );
  const containers = entries.filter(
    (entry) => entry.container_capacity_lb !== null,
  );

  async function addCustom(event: FormEvent) {
    event.preventDefault();
    const normalized = name.trim();
    if (!normalized) return;
    await run("add_inventory_item", {
      name: normalized,
      quantity,
      unit_weight_lb: unitWeight,
    });
    setName("");
    setQuantity(1);
    setUnitWeight(0);
  }

  return (
    <section className="tool-panel inventory-panel">
      <div className="inventory-heading">
        <h2><Backpack size={19} /> Envanter</h2>
        <span className={inventory.derived.over_capacity ? "capacity over" : "capacity"}>
          <Scale size={14} />
          {inventory.derived.total_weight_lb}/{inventory.derived.carrying_capacity_lb} lb
        </span>
      </div>

      <div className="currency-row" aria-label="Para kesesi">
        {(["cp", "sp", "ep", "gp", "pp"] as const).map((denomination) => (
          <div key={denomination}>
            <span>{denomination.toUpperCase()}</span>
            <strong>{inventory.currency[denomination]}</strong>
            <div>
              <button
                aria-label={`1 ${denomination} azalt`}
                onClick={() => run("adjust_currency", { denomination, delta: -1 })}
                disabled={inventory.currency[denomination] === 0}
              >−</button>
              <button
                aria-label={`1 ${denomination} ekle`}
                onClick={() => run("adjust_currency", { denomination, delta: 1 })}
              >+</button>
            </div>
          </div>
        ))}
      </div>

      <button
        className="catalog-add"
        onClick={() => run("add_inventory_item", { catalog_id: "item:shield" })}
      >
        <PackagePlus size={15} /> Katalogdan Shield ekle
      </button>

      <form className="inventory-add" onSubmit={addCustom}>
        <label>
          <span>Custom item</span>
          <input
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            placeholder="Örn. Halat"
          />
        </label>
        <label>
          <span>Adet</span>
          <input
            type="number"
            min="1"
            max="1000000"
            value={quantity}
            onChange={(event) => setQuantity(Number(event.target.value))}
          />
        </label>
        <label>
          <span>lb/adet</span>
          <input
            type="number"
            min="0"
            step="0.1"
            value={unitWeight}
            onChange={(event) => setUnitWeight(Number(event.target.value))}
          />
        </label>
        <button type="submit" aria-label="Custom item ekle"><PackagePlus size={16} /></button>
      </form>

      {entries.length ? (
        <ul className="inventory-list">
          {entries.map((item) => {
            const parent = item.container_id
              ? inventory.entries[item.container_id]
              : undefined;
            return (
              <li key={item.id}>
                <div className="inventory-item-copy">
                  <strong>{item.name}</strong>
                  <small>
                    {item.quantity} × {item.unit_weight_lb} lb
                    {parent ? ` · ${parent.name} içinde` : ""}
                  </small>
                  <div className="item-tags">
                    {item.equipped && <span>Equipped</span>}
                    {item.attuned && <span>Attuned</span>}
                    {item.catalog_id && <span>SRD</span>}
                  </div>
                </div>
                <div className="inventory-actions">
                  {containers.length > 0 && !item.equipped && (
                    <select
                      aria-label={`${item.name} container seçimi`}
                      value={item.container_id ?? ""}
                      onChange={(event) => run("move_inventory_item", {
                        item_id: item.id,
                        container_id: event.target.value || null,
                      })}
                    >
                      <option value="">Taşınmıyor</option>
                      {containers
                        .filter((container) => container.id !== item.id)
                        .map((container) => (
                          <option key={container.id} value={container.id}>
                            {container.name}
                          </option>
                        ))}
                    </select>
                  )}
                  {item.equipment_slot && (
                    <button onClick={() => run(
                      item.equipped ? "unequip_item" : "equip_item",
                      { item_id: item.id },
                    )}>
                      {item.equipped ? "Çıkar" : "Kuşan"}
                    </button>
                  )}
                  {item.requires_attunement && (
                    <button onClick={() => run(
                      item.attuned ? "unattune_item" : "attune_item",
                      { item_id: item.id },
                    )}>
                      {item.attuned ? "Bağı kes" : "Attune"}
                    </button>
                  )}
                  {!item.equipped && !item.attuned && !item.container_capacity_lb && (
                    <>
                      <button
                        aria-label={`${item.name} adedini azalt`}
                        disabled={item.quantity === 1}
                        onClick={() => run("set_inventory_quantity", {
                          item_id: item.id,
                          quantity: item.quantity - 1,
                        })}
                      >−</button>
                      <button
                        aria-label={`${item.name} adedini artır`}
                        onClick={() => run("set_inventory_quantity", {
                          item_id: item.id,
                          quantity: item.quantity + 1,
                        })}
                      >+</button>
                    </>
                  )}
                  <button
                    className="danger-icon"
                    aria-label={`${item.name} sil`}
                    disabled={item.equipped || item.attuned}
                    onClick={() => run("remove_inventory_item", { item_id: item.id })}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">Envanter boş.</p>
      )}

      <small className="inventory-summary">
        <CircleDollarSign size={13} />
        Coin ağırlığı {inventory.derived.coin_weight_lb} lb · Attunement{" "}
        {inventory.derived.attuned_count}/3 · Politika{" "}
        {inventory.encumbrance_policy}
      </small>
    </section>
  );
}
