Project: Venezuelan Carnet Photo Sheet Maker

Role

Act as a Python developer. Build a small, reliable, maintainable utility that converts an already-edited carnet photograph into a printable sheet containing six identical copies.

The user is responsible for editing the original photograph using a separate Gemini Gem. Your application must not perform AI image generation, facial editing, beautification, background removal, or any other modification to the subject.

Your responsibility is strictly the layout and document-generation stage.

1. Project context

The workflow is:

1. The user provides a portrait to a Gemini Gem specialized in Venezuelan carnet photographs.
2. Gemini returns the finished individual photograph.
3. The user supplies that photograph to this Python utility.
4. The utility generates a print-ready PDF containing six copies of the same photograph.

The goal is to eliminate the manual process of opening PowerPoint, importing the photograph, duplicating it six times, aligning the copies, adjusting the page, and exporting the result for printing.

The application should be lightweight, local, easy to use, and require minimal maintenance.

2. Reference document

The user has supplied a PDF named "saulelelegido.pdf".

Inspect this PDF if it is available in the project workspace. Use it as a visual reference for the existing printing workflow.

The reference shows:

- Six identical carnet photographs.
- All six photographs arranged in one horizontal row.
- The photographs occupy the upper portion of the page.
- The remaining lower portion of the page is mostly empty white space.
- Each photograph is vertical, approximately 3:4 in width-to-height ratio.
- The photographs have thin rectangular outlines or boundaries.

Important: The uploaded PDF's recorded page dimensions are approximately 595.2 × 765.36 PDF points. These do not match standard US Letter dimensions. Therefore, do not blindly copy its page dimensions.

The intended output page must be standard US Letter, portrait orientation:

- Width: 8.5 inches.
- Height: 11 inches.
- PDF dimensions: 612 × 792 points.

The reference photograph frames measure approximately 85.79 × 114.14 points, or 1.19 × 1.59 inches. Treat these as approximate reference measurements, not mandatory permanent dimensions.

If the reference PDF is unavailable in the development environment, continue using the requirements in this prompt and report that the reference could not be inspected.

3. Core functionality

Implement a Python utility that:

1. Accepts one input photograph.
2. Creates exactly six copies of that photograph.
3. Places the copies on one standard US Letter portrait page.
4. Preserves the photograph's aspect ratio.
5. Uses consistent dimensions and spacing for every copy.
6. Centers the overall arrangement horizontally.
7. Positions the arrangement near the top of the page, similar to the supplied reference.
8. Leaves the remaining page area white.
9. Exports a PDF suitable for printing at the intended physical dimensions.

The utility must not stretch, squash, rotate, crop, or otherwise modify the source photograph unexpectedly.

Use the same source image for all six copies. Do not generate six independently edited versions.

4. Layout requirements

The default arrangement should reproduce the reference's six-photo horizontal strip.

However, implement the layout so that its settings can be changed without rewriting the entire program.

At minimum, make these parameters configurable:

- Paper size, defaulting to US Letter.
- Page orientation, defaulting to portrait.
- Number of copies, defaulting to six.
- Number of columns, defaulting to six.
- Individual photo width and height.
- Horizontal and vertical spacing.
- Top margin.
- Left and right margins.
- Whether thin borders or cutting guides are drawn.

The default output should use six columns and one row.

If the selected dimensions cannot fit inside the printable page area, do not silently resize or distort the photographs. Display a clear error or offer an explicitly defined fit-to-page option.

When fit-to-page is enabled, preserve the photograph's aspect ratio and explain the resulting dimensions to the user.

5. Input and image handling

Support common image formats such as JPEG, PNG, and WebP if the chosen libraries support them reliably.

Requirements:

- Accept a user-selected image file.
- Handle images with EXIF orientation correctly.
- Preserve the source image's visual content.
- Avoid unnecessary recompression or quality loss.
- Handle transparent images appropriately, using a white background if transparency must be flattened for PDF output.
- Display a useful error if the image cannot be opened.
- Do not overwrite the original image.

The program should not require an internet connection.

6. Output requirements

Generate a PDF with:

- Exactly one page.
- Standard US Letter dimensions.
- Six identical photographs by default.
- White page background.
- Consistent placement and spacing.
- Optional subtle rectangular borders or cutting guides.
- No titles, labels, logos, watermarks, decorative elements, or extra text by default.

The photographs must be embedded or placed in a way that preserves their intended physical dimensions.

The output filename should be predictable and should not overwrite the input photograph.

The program should report the output file's location when generation succeeds.

7. Technology choices

Prefer a small, straightforward Python implementation.

Consider:

- Pillow for image loading, orientation correction, and image handling.
- ReportLab for generating the PDF with exact page dimensions and precise element placement.

Use additional dependencies only when they provide a clear benefit.

Avoid unnecessary frameworks, databases, cloud services, AI APIs, external accounts, or complex architectural patterns.

The application should run locally on Linux, including Fedora KDE.

8. User interface

For the first version, prioritize a functional and easy-to-use interface over visual sophistication.

A simple command-line interface is acceptable if it is genuinely convenient for the intended workflow.

Prefer a file picker or a lightweight graphical interface if it can be implemented without adding excessive dependencies or maintenance burden.

The ideal user experience is:

1. Select the finished carnet photograph.
2. Optionally adjust the layout settings.
3. Click or execute Generate.
4. Receive the PDF.

Do not build a large desktop application unless the simpler implementation proves insufficient.

9. Validation and error handling

Implement validation for:

- Missing input files.
- Unsupported or corrupted images.
- Invalid dimensions or spacing.
- Layouts that exceed the page boundaries.
- Invalid output locations.
- PDF-generation failures.

Provide clear, actionable error messages.

Do not silently produce a malformed PDF or claim that the output is ready when generation fails.

10. Testing and acceptance criteria

Create tests or a verification procedure demonstrating that:

1. The output is a valid, single-page PDF.
2. The page dimensions are exactly US Letter: 612 × 792 points.
3. The default layout contains exactly six photograph placements.
4. All six placements use the same source image.
5. Each photograph has the configured physical dimensions.
6. The photographs preserve their original aspect ratio.
7. All placements remain within the page boundaries.
8. The layout is horizontally centered.
9. Invalid input and impossible layouts produce understandable errors.
10. The original input image remains unchanged.

Include a way to inspect or preview the generated page before printing, if practical.

11. Deliverables

Provide:

- The complete Python source code.
- A dependency list and installation instructions.
- Instructions for running the utility on Fedora Linux.
- A brief explanation of the layout settings.
- Tests or a reproducible validation procedure.
- A sample generated PDF if a suitable input photograph is available.

Keep the implementation small and understandable. Explain any assumptions that could affect the physical print dimensions.

12. Scope boundaries

Do not implement:

- AI photo editing.
- Facial recognition or biometric analysis.
- Automatic identity correction.
- Cloud uploads or external API calls.
- User accounts or databases.
- Batch processing unless it is trivial to add without complicating the initial implementation.

Focus on producing a precise, repeatable, local PDF layout from one finished carnet photograph.

Begin by inspecting the supplied reference PDF, proposing a minimal implementation plan, and then implementing and validating the utility.
