/** Bill form helpers: line math, validation and conversion to/from the API shape. */
import { todayISO } from './format';

export const WORK_TYPES = ['JCB Work', 'Lorry Service', 'Tractor Service', 'Other Work'];

export const LORRY_MATERIALS = [
  'M Sand', 'P Sand', 'Hill Soil', 'Gravel Soil', 'Lake Soil', '1/2 Stone', '1/4 Stone', 'Black Stone',
];

export const UNIT_OPTIONS = {
  'JCB Work': ['hrs'],
  'Lorry Service': ['Ton', 'Unit', 'CFT', 'Load'],
  'Tractor Service': ['Hours', 'Trip', 'Load'],
  'Other Work': ['job'],
};

// Default unit per work type (used by Daily Work and as the starting unit on bills)
export const UNITS = {
  'JCB Work': 'hrs',
  'Lorry Service': 'Load',
  'Tractor Service': 'Hours',
  'Other Work': 'job',
};

const round2 = (value) => Math.round((Number(value) || 0) * 100) / 100;

/** Mirrors backend calculations.line_amount: quantity × rate + bata. */
export function lineAmount(item) {
  return round2((Number(item.quantity) || 0) * (Number(item.rate) || 0) + (Number(item.bata) || 0));
}

export function billTotal(items) {
  return round2(items.reduce((sum, item) => sum + lineAmount(item), 0));
}

let sequence = 0;
// crypto.randomUUID is unavailable on plain http:// LAN addresses, so use a counter
const nextId = () => `item-${Date.now()}-${sequence++}`;

/**
 * Rate and bata default only for JCB Work (from Settings).
 * Lorry and Tractor rates are entered by hand, so they start empty.
 */
export function newWorkItem(defaults, date) {
  return {
    id: nextId(),
    date,
    workType: 'JCB Work',
    material: '',
    description: '',
    unit: UNITS['JCB Work'],
    quantity: '',
    rate: defaults.jcbRate ?? '',
    bata: defaults.defaultBata ?? '',
  };
}

export function applyWorkType(item, workType, defaults) {
  const isJcb = workType === 'JCB Work';
  return {
    ...item,
    workType,
    material: '',
    unit: UNITS[workType],
    rate: isJcb ? (defaults.jcbRate ?? '') : '',
    bata: isJcb ? (defaults.defaultBata ?? '') : '',
  };
}

export function newBillForm(defaults) {
  const today = todayISO();
  return {
    billDate: today,
    customerName: '',
    customerAddress: '',
    projectName: '',
    items: [newWorkItem(defaults, today)],
  };
}

export function formFromBill(bill) {
  return {
    billDate: bill.bill_date,
    customerName: bill.customer_name,
    customerAddress: bill.customer_address || '',
    projectName: bill.project_name || '',
    items: bill.items.map((item) => ({
      id: `saved-${item.item_id}`,
      date: item.date,
      workType: item.work_type,
      material: item.work_type === 'Lorry Service' ? item.description : '',
      description: item.work_type === 'Lorry Service' ? '' : item.description,
      unit: item.unit,
      quantity: String(item.quantity),
      rate: String(item.rate),
      bata: String(item.bata),
    })),
  };
}

export function validateBill(form) {
  const errors = {};
  if (!form.billDate) errors.billDate = 'Bill date is required.';
  if (!form.customerName.trim()) errors.customerName = 'Customer name is required.';
  if (form.items.length === 0) errors.items = 'Add at least one work item.';

  const itemErrors = form.items.map((item) => {
    const found = {};
    if (!item.workType) found.workType = 'Work type is required.';
    if (!item.date) found.date = 'Date is required.';
    if (item.workType === 'Lorry Service' && !LORRY_MATERIALS.includes(item.material)) {
      found.material = 'Choose a material.';
    }
    if (!(UNIT_OPTIONS[item.workType] || []).includes(item.unit)) found.unit = 'Choose a unit.';
    if (item.workType === 'Other Work' && !item.description.trim()) {
      found.description = 'Description is required.';
    }
    if (!(Number(item.quantity) > 0)) found.quantity = 'Quantity must be greater than zero.';
    if (item.rate === '') found.rate = 'Enter the rate.';
    else if (Number(item.rate) < 0) found.rate = 'Rate cannot be negative.';
    if (item.bata !== '' && Number(item.bata) < 0) found.bata = 'Bata cannot be negative.';
    return found;
  });

  if (itemErrors.some((found) => Object.keys(found).length > 0)) errors.itemErrors = itemErrors;
  if (!errors.items && billTotal(form.items) <= 0) {
    errors.items = 'The bill total must be more than zero.';
  }
  return errors;
}

export function hasErrors(errors) {
  return Object.keys(errors).length > 0;
}

export function toPayload(form) {
  return {
    bill_date: form.billDate,
    customer_name: form.customerName.trim(),
    customer_address: form.customerAddress.trim(),
    project_name: form.projectName.trim(),
    items: form.items.map((item) => ({
      date: item.date,
      work_type: item.workType,
      material: item.workType === 'Lorry Service' ? item.material : '',
      description: item.description.trim(),
      quantity: Number(item.quantity),
      unit: item.unit,
      rate: Number(item.rate),
      bata: item.bata === '' ? 0 : Number(item.bata),
    })),
  };
}
