const express = require('express');
const path = require('path');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, '..', 'frontend')));

function round2(value) {
  return Math.round(value * 100) / 100;
}

function calculateSip({ monthlyInvestment, annualReturnRate, years }) {
  const totalMonths = Math.round(years * 12);
  const monthlyRate = annualReturnRate / 12 / 100;

  const valueAfterMonths = (months) => {
    if (months <= 0) return 0;
    if (monthlyRate === 0) return monthlyInvestment * months;
    return (
      monthlyInvestment *
      ((Math.pow(1 + monthlyRate, months) - 1) / monthlyRate) *
      (1 + monthlyRate)
    );
  };

  const totalValue = valueAfterMonths(totalMonths);
  const investedAmount = monthlyInvestment * totalMonths;
  const estimatedReturns = totalValue - investedAmount;

  const yearlyBreakdown = [];
  const wholeYears = Math.ceil(years);
  for (let year = 1; year <= wholeYears; year++) {
    const months = Math.min(year * 12, totalMonths);
    const invested = monthlyInvestment * months;
    const value = valueAfterMonths(months);
    yearlyBreakdown.push({
      year,
      invested: round2(invested),
      value: round2(value),
      returns: round2(value - invested),
    });
  }

  return {
    investedAmount: round2(investedAmount),
    estimatedReturns: round2(estimatedReturns),
    totalValue: round2(totalValue),
    yearlyBreakdown,
  };
}

function validateInput(body) {
  const { monthlyInvestment, annualReturnRate, years } = body;
  const numbers = { monthlyInvestment, annualReturnRate, years };
  for (const [key, value] of Object.entries(numbers)) {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      return `${key} must be a number`;
    }
  }
  if (monthlyInvestment <= 0) return 'monthlyInvestment must be greater than 0';
  if (years <= 0 || years > 50) return 'years must be between 0 and 50';
  if (annualReturnRate < 0 || annualReturnRate > 100) return 'annualReturnRate must be between 0 and 100';
  return null;
}

app.post('/api/sip/calculate', (req, res) => {
  const validationError = validateInput(req.body || {});
  if (validationError) {
    return res.status(400).json({ error: validationError });
  }
  const result = calculateSip(req.body);
  res.json(result);
});

const PORT = process.env.PORT || 4000;
app.listen(PORT, () => {
  console.log(`SIP calculator backend running on http://localhost:${PORT}`);
});
