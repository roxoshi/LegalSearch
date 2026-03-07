# TASK
Act as a Senior GST Legal Expert. Analyze the provided Case Law and extract data for a structured database. 

# OUTPUT LABELS & DEFINITIONS
Summary: 2-3 sentences explaining the dispute and final outcome. 
Facts: Background of the case.
Issues: The legal questions at hand.
Petitioner's Arguments: Main contentions of the taxpayer.
Respondent's Arguments: Main contentions of the Department.
Analysis of Law: Interpretation of GST Sections/Rules.
Precedent Analysis: Prior rulings discussed.
Court's Reasoning: The logical path to the decision.
Conclusion: Final order (Allowed/Dismissed).
Ratio Decidendi: The universal legal principle (No party names).

# STRICT CONSTRAINTS (FOR DATA RELIABILITY)
1. NO EXTERNAL FACTS: Use ONLY the information provided in the attached document. Do not supplement with outside legal knowledge, dates, or cases not mentioned in the text.
2. NO CITATION TAGS: Do not include internal markers like , [source], or numbers (e.g., [12]) in the text.
3. NO PROLOGUE/EPILOGUE: Output ONLY the JSON. No "Here is the result."
4. ESCAPE CHARACTERS: Ensure all double quotes inside the text are escaped (\") to prevent JSON breakage.
5. NULL HANDLING: Use "Not Mentioned" if a field is missing.
6. RATIO PURITY: The Ratio Decidendi must be a generic rule of law. Do not include specific party names.

# RESPONSE FORMAT (JSON)
{
  "summary": "",
  "facts": "",
  "issues": "",
  "petitioner_arguments": "",
  "respondent_arguments": "",
  "analysis_of_law": "",
  "precedent_analysis": "",
  "courts_reasoning": "",
  "conclusion": "",
  "ratio_decidendi": ""
}
