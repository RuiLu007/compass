import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import schema from "../generated/model-contracts.schema.json";

const ajv = new Ajv2020({ allErrors: false, strict: false });
addFormats(ajv);
ajv.addSchema(schema, "compass-models");
const validators = new Map<string, ValidateFunction>();
export function isModelContract(name: string, value: unknown): boolean {
  if (!Object.hasOwn(schema.$defs, name)) return false;
  let validate = validators.get(name);
  if (!validate) {
    validate = ajv.compile({ $ref: `compass-models#/$defs/${name}` });
    validators.set(name, validate);
  }
  return !!validate(value);
}
export function parseModelContract<T>(name: string, value: unknown): T {
  if (!isModelContract(name, value)) throw new Error("Incompatible model contract");
  return value as T;
}
