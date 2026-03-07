List of changes

There is a major change in the frontend and data pipelines

Current state:
Earlier the pipeline was filtering cases for gst using an NER model and for the filtered cases the pdf was taken from .data/gst_pdfs/ and converted to text the text is used to create and store vector embeddings and also the pdf is converted to html and stored in db to be rendered for frontend later.

Required state:
- Now the vectors will be created from the case summary json files in the folder .data/batch_analysis/successes
- The value of each key of the json will be one chunk and its vector created will be stored in the document_chunks table in postgres which exists. Delete the old chunks from the table
- Remove the step of converting the pdfs to text and then html. The frontend will now show the contents of the json file above as html. For that create a good html format by taking texts from the jsons and putting them under correct headings with good formating. Add this html in the place of current html in the documents postgres table.
- When the user searches for a text in the search bar the backend runs vector search on the chunks from the jsons and the top 10 results are shown in search. When the user clicks a result they see a webpage created using the text from the json text (not the json file as it is).
- On the rendered webpage the user should also have an option on the right to download the original full pdf. The pdf files are kept in the .data/gst_pdfs folder and the name or pdf and json for the same case is same (which is the case id)

Additional instructions:
- Edit existing codes wherever possible without leaving old dead code in the repository
- all new additional should have tests with high coverage
- add instructions in the docs folder on how to recreate the whole website again
- I do not want to run the steps of extracting the zip files of each year and getting the gst cases from it because the gst_pdfs folder already have those pdf files required.
- But your new changes should fully compatible with running the full pipeline end-to-end
