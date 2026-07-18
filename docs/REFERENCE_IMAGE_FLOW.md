# Reference Image Flow

## Goal

Add a second, deliberately simple generation path alongside the existing structured ad-copy pipeline.

The reference flow sends the selected image engine only:

- one reference image
- the selected persona seed
- the product master document
- the current product image upload set
- a short generation instruction
- the required 4:5 safe-zone and aspect-ratio rules

It does not send format rules, visual-pattern selections, hypothesis settings, copy architecture, or the normal starting prompt.

## User flow

1. Switch from **Structured Flow** to **Reference Image Flow** using the top mode switch.
2. Select one or more personas from `persona_seeds.json`.
3. Upload a folder of reference images, or select individual images.
4. Optionally override the product document or add product images.
5. Select Gemini or ChatGPT.
6. Run the batch.
7. The system processes all reference images for persona 1, then persona 2, and so on.
8. Each 4:5 image is generated first. When enabled, the existing 4:5-to-9:16 conversion flow runs afterward.

For 25 reference images and 5 personas, the run creates 125 independent 4:5 jobs before the optional 9:16 conversion.

## Prompt contract

The base instruction remains intentionally simple:

> I have uploaded a reference image. Create an ad for my product in the style of the reference image. I have uploaded the reference image, my product, and my product doc.

The backend adds only the selected persona context, product document, product-image/reference-image role labels, 4:5 output requirement, and an 8% protected safe margin. The model is responsible for understanding the reference image and deciding the composition, copy treatment, and visual direction.

## Storage

A reference run uses the normal run and batch system. Its manifest contains `flow_type: reference_image`.

Generated images are grouped by persona under the batch aspect folder:

```text
generated_images/<batch>/4_5/<persona_slug>/generated images/
generated_images/<batch>/9_16/<persona_slug>/generated images/
```

The run inputs, prompt files, selected personas, reference-image mapping, progress, failures, and engine selection are stored under `dashboard_storage/runs/<run_id>/`.

## Image comments and revisions

Every active gallery image in both flows has a **Comment & revise** control.

The revision request sends:

- the current generated image
- the original image-generation prompt when available
- the current product images
- the user's exact revision comment
- the selected Gemini or ChatGPT engine
- the original aspect ratio

The previous image is archived before the replacement takes its place. Revision status is polled independently so the rest of the dashboard remains usable.

## API additions

- `POST /api/runs/execute-reference`
- `GET /api/runs/{run_id}/reference-status`
- `POST /api/runs/{run_id}/revise-image`
- `GET /api/runs/{run_id}/revisions/{revision_id}`

Existing generation, cancellation, run listing, download, and 9:16 conversion endpoints are reused.

## Safety and compatibility decisions

- The existing `/api/runs/execute` structured pipeline is unchanged.
- Reference work runs in a background thread and reports progress through a run status file.
- Cancellation is checked between jobs and reuses the existing run cancellation signal.
- Prompt files and image-source files are generated per job to avoid attaching all reference images to every prompt.
- Status polling includes a cache-busting query because the dashboard API helper caches GET requests.
- The new backend logic is isolated in `dashboard/backend/reference_flow.py`; route files only expose its endpoints.

## Validation checklist

- [x] Python syntax compilation for the new backend module and modified route modules
- [x] JavaScript syntax checks for both new frontend modules
- [x] Reference prompt contract includes persona, product doc, 4:5, and safe-zone rules
- [x] Existing structured route remains unchanged
- [x] Multi-reference × multi-persona job count is explicit in the UI
- [x] Engine selection is available for reference generation and image revisions
- [x] Reference run output paths include persona grouping
- [x] 9:16 conversion reuses the existing conversion implementation
- [x] Per-image comments are added to galleries from both flows

A real browser-generation smoke test still requires a machine with the repository's configured Chrome/CDP session and authenticated Gemini/ChatGPT accounts.
