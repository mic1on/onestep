/**
 * Reconstruct CanonicalValue trees from the tagged fixture format.
 *
 * The golden generator tags every value ("float", "int", "str", ...) because
 * JSON cannot distinguish `1` from `1.0` — and the submission digest does.
 * Shared by the unit and end-to-end tests.
 */

import { PyFloat, type CanonicalValue } from '../src/canonical.ts';

/** A typed fixture node, as emitted by generate_golden.py's tag_types(). */
export type Tagged =
  | { __t: 'none' }
  | { __t: 'bool'; v: boolean }
  | { __t: 'int'; v: number }
  | { __t: 'float'; v: string }
  | { __t: 'str'; v: string }
  | { __t: 'list'; v: Tagged[] }
  | { __t: 'dict'; v: { [key: string]: Tagged } };

/** Rebuild a CanonicalValue from its tagged fixture form. */
export function fromTagged(node: Tagged): CanonicalValue {
  switch (node.__t) {
    case 'none':
      return null;
    case 'bool':
      return node.v;
    case 'int':
      return node.v;
    case 'float':
      return new PyFloat(Number(node.v));
    case 'str':
      return node.v;
    case 'list':
      return node.v.map(fromTagged);
    case 'dict': {
      const out: { [key: string]: CanonicalValue } = {};
      for (const [k, v] of Object.entries(node.v)) out[k] = fromTagged(v);
      return out;
    }
    default: {
      const exhaustive: never = node;
      throw new Error(`unknown tag: ${JSON.stringify(exhaustive)}`);
    }
  }
}
