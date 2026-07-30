# Catalog schema v2 prerequisites

## Decision

The schema v1 curation pack is deliberately **draft-only**. It must not be marked
builder-ready, published, or made the default ruleset. The current seven entity
shapes can hold a small subset of SRD 5.2.1 facts, but they cannot represent the
complete rules needed by the builder, character sheet, inventory, or action engine.

The pinned source is the official SRD 5.2.1 PDF identified by SHA-256
`8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87`.
Closed D&D Beyond compendium or builder content is outside this pipeline.

## Missing semantics

| Entity | Schema v1 can safely hold | Schema v2 prerequisite |
| --- | --- | --- |
| Class | Hit Die, primary ability, saves, armor training, skill-choice policy, average HP, feature references | Weapon/tool proficiencies, starting equipment choices, level table, multiclass traits, typed feature-choice references, spell progression separated by cantrip/prepared/known policy |
| Species | Creature type, size, speed, trait names | Typed trait choices, granted proficiency/language/spell/resource/effect records, level-scaled traits |
| Background | Ability choices, feat display name, two skills, tool display name | Stable feat/tool references, equipment packages, currency alternative, replacement/custom-background rules |
| Spell | Level, school, casting time, range, components, duration, healing, healing upcast | Targets/areas, attack/save model, damage/healing effects, conditions, concentration/ritual/material metadata, general upcast and cantrip scaling |
| Feature | Class owner, level, activation, display effect, simple uses/recovery | Species/background/feat ownership, typed choices, prerequisites, effect operations, action-economy and resource bindings |
| Item | Weight, GP cost, simple AC bonus, training, slot, attunement, pound capacity | Weapon damage/properties/mastery, armor base-AC formulas and Dexterity cap, consumable effects, charges, magic rarity, quantities/units, general container volume |

## Required release gates

1. Add an explicit catalog schema version and a migration path; never reinterpret
   already-published schema v1 rows.
2. Validate every new typed reference and reject dangling or wrong-type references.
3. Separate descriptive catalog data from executable operations. Engines remain
   authoritative for rolls, resources, action economy, HP, conditions, and inventory.
4. Add new-database and prior-schema migration tests plus malformed/future-version
   rejection tests.
5. Add builder and sheet tests for every supported class spell/proficiency policy.
6. Run a manual source/provenance review against the pinned PDF before publication.
7. Publish to a new immutable ruleset ID; only then may a developer explicitly make
   it the default for newly created campaigns.

## Current curation pack

`data/catalog-curation/srd-5.2.1-foundation-expansion-1.json` contains only facts
that fit schema v1 without inventing unsupported fields:

- 2 class summaries and 7 referenced level-one feature summaries
- 4 species summaries
- 3 background summaries
- 1 healing spell
- 4 pound-capacity containers

The importer records capability and known-gap declarations in its result, refuses
silent overwrites, and refuses to publish this pack.
